import React, { useState } from 'react'
import { HS } from './readiness/healthModel.js'

/**
 * ReadinessBanner — the persistent amber line that says this deployment is not
 * whole, above whichever surface is mounted.
 *
 * WHY IT IS GLOBAL AND NOT A PAGE
 * -------------------------------
 * The two deployments that break hardest are the ones where SimCore says `ok`:
 * booted without `tools/` (every adapter_ref unresolvable, the payload shelf can
 * stage nothing) and booted without `scenarios/` (nothing to launch). In both,
 * `/api/health` has answered `status: "ok"` — so the DC's first signal that
 * anything is wrong is a Library with no rows, which reads as "this product has
 * no content" rather than "this container is missing a COPY".
 *
 * A readiness PAGE fixes that only for the operator who visits it. This banner
 * follows them onto every destination and carries the FIX, not just the fault.
 *
 * Dismissal is per-session and per-fingerprint: dismissing "adapter_catalog
 * degraded" must not also silence a payload-shelf regression that appears ten
 * minutes later. A banner that can be permanently silenced is a banner that
 * will be, on the day it matters.
 */
export default function ReadinessBanner({ model = null, onNavigate = () => {} }) {
  const [dismissedFingerprint, setDismissedFingerprint] = useState(null)

  // Never probed, or unreachable — the unreachable case has its own louder
  // banner (`api-down`) and duplicating it here would just add noise.
  if (!model || model.reachable === false) return null
  if (!model.degraded.length) return null

  const fingerprint = model.degraded.slice().sort().join(',')
  if (dismissedFingerprint === fingerprint) return null

  const rows = model.components.filter(
    (c) => c.status === HS.DEGRADED || c.status === HS.ERROR,
  )

  return (
    <div className="readiness-banner" role="status" data-testid="readiness-banner">
      <div className="readiness-banner__head">
        <span className="readiness-banner__tag">
          {model.overall === HS.ERROR ? 'SIMCORE ERROR' : 'SIMCORE DEGRADED'}
        </span>
        <span className="readiness-banner__count mono" data-testid="readiness-banner-count">
          {model.degraded.length} component{model.degraded.length === 1 ? '' : 's'}
        </span>
        <button
          type="button"
          className="btn btn--xs"
          onClick={() => onNavigate('readiness')}
          data-testid="readiness-banner-open"
        >
          Readiness ▸
        </button>
        <button
          type="button"
          className="readiness-banner__x"
          aria-label="Dismiss readiness warning for this session"
          onClick={() => setDismissedFingerprint(fingerprint)}
        >
          ✕
        </button>
      </div>
      <ul className="readiness-banner__list">
        {rows.map((c) => (
          <li key={c.key} data-testid={`readiness-banner-${c.key}`}>
            <span className="mono">{c.label}</span>
            {c.count !== null && <span className="mono"> ({c.count})</span>}
            {' — '}
            {c.disagreement || c.detail || c.remediation}
            {c.disagreement && c.remediation && (
              <span className="readiness-banner__fix"> → {c.remediation}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
