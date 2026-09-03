import React, { useCallback, useState } from 'react'

/**
 * SafetyBanner — the blast-radius warning that sits above everything.
 *
 * NOT the same banner as `ReadinessBanner`, and the two must never be merged.
 * ReadinessBanner answers "is this deployment whole?" (a SimCore fault, amber,
 * dismissable per-fingerprint). This one answers "do you understand what
 * pressing Launch does to the customer's tenant?" — it is not a fault report at
 * all, it is a consent gate, and it is true even on a perfectly healthy
 * deployment. Folding it into the readiness line would make it disappear on the
 * exact deployment where everything works and a DC is therefore most likely to
 * launch without reading.
 *
 * WHY IT ACKNOWLEDGES RATHER THAN DISMISSES
 * -----------------------------------------
 * The button says Acknowledge, and the acknowledgement is scoped to the TENANT
 * (`tenantId`), persisted in localStorage. Scoping matters: a DC who
 * acknowledged the blast radius against their own lab tenant has not
 * acknowledged it against the customer tenant they point at forty minutes
 * later, and re-arming on a tenant switch is the whole point. A global
 * "dismiss forever" would be silence on the day it matters — the same reasoning
 * ReadinessBanner applies to its own per-fingerprint dismissal.
 *
 * Storage failure is not a reason to hide a safety notice: if localStorage
 * throws (private mode, storage disabled), the read falls back to "not
 * acknowledged" and the banner shows. Failing loud is the correct direction for
 * this one.
 *
 * Props:
 *   tenantId  — active tenant name/id, or null. Acknowledgement is keyed on it.
 *   onNavigate — (destinationId) => void, used by "Review scope".
 */

const LS_PREFIX = 'cortexsim.blastRadiusAck.'

/** Storage key for one tenant. A null tenant gets its own key rather than
 *  sharing the first tenant's — "no tenant selected" is its own scope. */
export function ackKey(tenantId) {
  return LS_PREFIX + (tenantId || '__none__')
}

export function readAck(tenantId) {
  try {
    return window.localStorage.getItem(ackKey(tenantId)) === 'true'
  } catch {
    // Storage unavailable — show the warning. See the doc comment above.
    return false
  }
}

export default function SafetyBanner({ tenantId = null, onNavigate = () => {} }) {
  // Keyed by tenant so a tenant switch re-arms the banner without a reload:
  // `useState` alone would keep the previous tenant's answer, so the acked
  // tenant is tracked explicitly and compared against the current one.
  const [ackedTenant, setAckedTenant] = useState(() => (readAck(tenantId) ? tenantId : undefined))

  const acknowledge = useCallback(() => {
    try { window.localStorage.setItem(ackKey(tenantId), 'true') } catch { /* ignore */ }
    setAckedTenant(tenantId)
  }, [tenantId])

  // `undefined` is the never-acknowledged sentinel; a null tenantId is a real,
  // acknowledgeable scope, so `=== null` must NOT read as "already acked".
  const acknowledged = ackedTenant !== undefined && ackedTenant === tenantId
    ? true
    : readAck(tenantId)

  if (acknowledged) return null

  const scope = tenantId || 'the active tenant'

  return (
    <div className="safety-banner" role="alert" data-testid="safety-banner">
      <div className="safety-banner__tag">Warning · Detection Testing</div>
      <span className="safety-banner__rule" aria-hidden="true" />
      <div className="safety-banner__msg">
        This console executes real adversary techniques against live agents and
        production-shaped tenants. Every launch writes telemetry to{' '}
        <strong className="mono">{scope}</strong> and leaves artifacts until teardown
        runs. Confirm your scope and blast radius before you launch.
      </div>
      <button
        type="button"
        className="safety-banner__link"
        onClick={() => onNavigate('readiness')}
        data-testid="safety-banner-review"
      >
        Review scope
      </button>
      <button
        type="button"
        className="safety-banner__ack"
        onClick={acknowledge}
        data-testid="safety-banner-ack"
      >
        Acknowledge
      </button>
    </div>
  )
}
