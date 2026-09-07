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
 *
 * WHY THE DETAIL IS FOLDED BY DEFAULT
 * ----------------------------------
 * Expanded, this banner ran 103px, and it is PERSISTENT — unlike the safety
 * banner, which leaves the grid once acknowledged. Stacked with the header,
 * phase bar and command strip it left 183px of a 620px viewport for actual
 * content (measured 70% chrome), which is what made the console read as "the
 * library is overlapping the system announcements".
 *
 * What folds is the per-component detail, never the claim. The tag
 * (DEGRADED/ERROR) and the component COUNT stay on the collapsed line, so the
 * Gate-A5 rule that a degraded deployment must never render as a healthy one
 * holds whether or not anyone expands it. The fault-and-fix text is one click
 * away, and the row starts expanded when the state is ERROR — at that point the
 * deployment is not merely incomplete and the detail is worth the height.
 */
export default function ReadinessBanner({ model = null, onNavigate = () => {} }) {
  const [dismissedFingerprint, setDismissedFingerprint] = useState(null)
  const [expanded, setExpanded] = useState(false)

  // Never probed, or unreachable — the unreachable case has its own louder
  // banner (`api-down`) and duplicating it here would just add noise.
  if (!model || model.reachable === false) return null
  if (!model.degraded.length) return null

  const fingerprint = model.degraded.slice().sort().join(',')
  if (dismissedFingerprint === fingerprint) return null

  const rows = model.components.filter(
    (c) => c.status === HS.DEGRADED || c.status === HS.ERROR,
  )

  const isError = model.overall === HS.ERROR
  const open = expanded || isError

  return (
    <div className="readiness-banner" role="status" data-testid="readiness-banner">
      <div className="readiness-banner__head">
        <span className="readiness-banner__tag">
          {isError ? 'SIMCORE ERROR' : 'SIMCORE DEGRADED'}
        </span>
        <span className="readiness-banner__count mono" data-testid="readiness-banner-count">
          {model.degraded.length} component{model.degraded.length === 1 ? '' : 's'}
        </span>
        {/* Hidden on ERROR: there the detail is always open, so a control that
            cannot close it would be a lie about what it does. */}
        {!isError && (
          <button
            type="button"
            className="readiness-banner__toggle"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={open}
            aria-controls="readiness-banner-detail"
            data-testid="readiness-banner-toggle"
          >
            {open ? '▾' : '▸'} {open ? 'Hide' : 'Details'}
          </button>
        )}
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
      {open && (
      <ul className="readiness-banner__list" id="readiness-banner-detail">
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
      )}
    </div>
  )
}
