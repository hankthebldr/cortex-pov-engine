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
 * (`tenantId`) AND to the SESSION. Both scopes matter, for different reasons:
 *
 *   - Per tenant, because a DC who acknowledged the blast radius against their
 *     own lab tenant has not acknowledged it against the customer tenant they
 *     point at forty minutes later.
 *   - Per session, because the consent this gate collects is about the person
 *     at the keyboard right now. It was previously persisted in localStorage,
 *     which meant a DC acknowledged once and never saw it again — on any later
 *     day, in front of any later customer, including the first launch of a new
 *     engagement. An acknowledgement that survives the session it was given in
 *     is not consent, it is a remembered click.
 *
 * Hence sessionStorage: every new session re-arms the gate, and it re-arms
 * again on a tenant switch within a session.
 *
 * WHY IT MINIMIZES RATHER THAN DISAPPEARS
 * ---------------------------------------
 * Acknowledging used to unmount the banner entirely, so the loudest statement
 * of what this console does to a customer tenant vanished for the whole session
 * the moment it was read. It now collapses to a single compact line that keeps
 * the tenant name and the route back to scope on screen. The warning is
 * strongest when it is unread, and useful — quietly — after.
 *
 * Storage failure is not a reason to hide a safety notice: if sessionStorage
 * throws (private mode, storage disabled), the read falls back to "not
 * acknowledged" and the full banner shows. Failing loud is the correct
 * direction for this one.
 *
 * Props:
 *   tenantId  — active tenant name/id, or null. Acknowledgement is keyed on it.
 *   onNavigate — (destinationId) => void, used by "Review scope".
 */

const SS_PREFIX = 'cortexsim.blastRadiusAck.'

/** Storage key for one tenant. A null tenant gets its own key rather than
 *  sharing the first tenant's — "no tenant selected" is its own scope. */
export function ackKey(tenantId) {
  return SS_PREFIX + (tenantId || '__none__')
}

export function readAck(tenantId) {
  try {
    // sessionStorage, NOT localStorage — see "per session" above. Changing this
    // back would silently let one acknowledgement cover every future POV.
    return window.sessionStorage.getItem(ackKey(tenantId)) === 'true'
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
    try { window.sessionStorage.setItem(ackKey(tenantId), 'true') } catch { /* ignore */ }
    setAckedTenant(tenantId)
  }, [tenantId])

  // `undefined` is the never-acknowledged sentinel; a null tenantId is a real,
  // acknowledgeable scope, so `=== null` must NOT read as "already acked".
  const acknowledged = ackedTenant !== undefined && ackedTenant === tenantId
    ? true
    : readAck(tenantId)

  const scope = tenantId || 'the active tenant'

  // ── Acknowledged: the compact line. Still rendered, still `role="status"`,
  // still carries the tenant — see "why it minimizes" above.
  if (acknowledged) {
    return (
      <div
        className="safety-banner safety-banner--min"
        role="status"
        data-testid="safety-banner"
        data-state="acknowledged"
      >
        <span className="safety-banner__tag">Warning · Detection Testing</span>
        <span className="safety-banner__rule" aria-hidden="true" />
        <span className="safety-banner__msg">
          Live techniques write telemetry to <strong className="mono">{scope}</strong>.
        </span>
        <button
          type="button"
          className="safety-banner__link"
          onClick={() => onNavigate('readiness')}
          data-testid="safety-banner-review"
        >
          Review scope
        </button>
      </div>
    )
  }

  // ── Not acknowledged: the full-height gate.
  return (
    <div
      className="safety-banner safety-banner--full"
      role="alert"
      data-testid="safety-banner"
      data-state="pending"
    >
      <div className="safety-banner__head">
        <span className="safety-banner__tag">Warning · Detection Testing</span>
      </div>

      {/* The tenant is named ONCE, in the sentence that says what happens to it.
          An extra "Scope: <tenant>" chip in the head read as a second, separate
          fact and made the one line that matters skimmable past. */}
      <div className="safety-banner__msg">
        This console executes <strong>real adversary techniques</strong> against live
        agents and production-shaped tenants. Every launch writes telemetry to{' '}
        <strong className="mono">{scope}</strong> and leaves artifacts on the target
        until teardown runs. Confirm your scope and blast radius before you launch.
      </div>

      <div className="safety-banner__actions">
        <button
          type="button"
          className="safety-banner__ack"
          onClick={acknowledge}
          data-testid="safety-banner-ack"
        >
          Acknowledge
        </button>
        <button
          type="button"
          className="safety-banner__link"
          onClick={() => onNavigate('readiness')}
          data-testid="safety-banner-review"
        >
          Review scope
        </button>
      </div>
    </div>
  )
}
