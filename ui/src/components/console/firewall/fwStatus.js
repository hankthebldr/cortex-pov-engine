/**
 * Status vocabulary for the Firewall wizard, mapped onto the chip classes the
 * Readiness surface already uses (healthModel.js HS_CHIP). One place, so a
 * stage and a source row can never colour the same status differently.
 *
 * An unrecognised status renders as UNKNOWN with the plain chip — never as ok.
 */
export const FW_CHIP = {
  ok: 'chip chip--detected',
  ready: 'chip chip--detected',
  degraded: 'chip chip--pending',
  blocked: 'chip chip--missed',
  skipped: 'chip',
}

export function chipFor(status) {
  return FW_CHIP[status] || 'chip'
}

export function statusLabel(status) {
  return status ? String(status).toUpperCase() : 'UNKNOWN'
}

const RANK = { ok: 0, ready: 0, skipped: 1, degraded: 1, blocked: 2 }

/**
 * A source row's status: the server's, raised to blocked when the row itself
 * carries a NAT-between verdict or a collision. The UI never shows a row
 * kinder than its own facts.
 */
export function sourceStatus(src) {
  let status = src?.status || 'unknown'
  const blockedFacts = src?.nat_verdict === 'nat_between'
    || (Array.isArray(src?.collision_with) && src.collision_with.length > 0)
  if (blockedFacts) status = 'blocked'
  if (!(status in RANK)) return 'unknown'
  return status
}

/** Firewall-observed source provenance, as the spec words it. */
export function srcProvenanceLabel(p) {
  if (p === 'inferred:last_ip') return 'inferred from last_ip'
  if (p === 'entered') return 'entered'
  return 'unknown'
}

/** Agent-side address provenance. */
export function addrProvenanceLabel(p) {
  if (p === 'entered') return 'entered'
  if (p === 'reported') return 'reported'
  return 'unknown'
}

/** Profile types whose actions v1 does not classify. */
export const NOT_EVALUATED_PROFILES = ['url-filtering', 'wildfire-analysis']
export const EVALUATED_PROFILES = ['virus', 'spyware', 'vulnerability']
