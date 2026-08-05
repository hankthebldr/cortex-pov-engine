import React, { useState } from 'react'
import { CopyButton } from './ProvenanceBlock.jsx'

/**
 * PayloadShelfBanner — the sentence a DC must read BEFORE the customer meeting.
 *
 * 50 of the 84 tool-adapter packs install their tool from the public internet
 * ON THE TARGET HOST at dispatch. The customers who buy Cortex run default-deny
 * egress, so that is the first thing their network blocks — and a step whose
 * tool never arrived RUNS ANYWAY, produces no detection, and the absent
 * detection reads in a POV report as "Cortex missed it".
 *
 * The banner is absent when there is nothing to warn about. A banner that is
 * always there is furniture nobody reads.
 */
export default function PayloadShelfBanner({
  counts,
  shelf,
  available,
  error = null,
  loading = false,
  onShowUnstaged = () => {},
  onRetry = null,
}) {
  const [why, setWhy] = useState(false)

  if (loading) {
    return (
      <div className="payload-banner payload-banner--muted" data-testid="payload-shelf-banner">
        <span className="payload-banner__tag mono">SHELF</span>
        <span className="payload-banner__msg mono">checking payload shelf…</span>
      </div>
    )
  }

  // ── The shelf could not be read ───────────────────────────────────────────
  if (available === false) {
    return (
      <div className="payload-banner payload-banner--unknown" role="status" data-testid="payload-shelf-banner">
        <span className="payload-banner__tag mono">SHELF UNKNOWN</span>
        <div className="payload-banner__body">
          <p className="payload-banner__msg">
            This SimCore does not report payload-shelf state
            {error?.code ? <> (<span className="mono">{error.code}</span>)</> : <> (<span className="mono">/api/shelf/payloads → 404</span>)</>}.
            Supply status below is <strong>UNKNOWN</strong> — neither staged nor unstaged.
            Nothing here should be read as "the target needs no egress".
          </p>
          {error?.message && <p className="payload-banner__detail mono">{error.message}</p>}
        </div>
        {onRetry && (
          <button type="button" className="btn btn--xs" onClick={onRetry}>↻ Retry</button>
        )}
      </div>
    )
  }

  const readOnly = shelf?.writable === false
  const nothingToSay = counts.target_egress === 0 && counts.unknown === 0 && !readOnly
    && !(shelf?.unbound?.length)
  if (nothingToSay) return null

  return (
    <div
      className={'payload-banner' + (readOnly ? ' payload-banner--readonly' : '')}
      role="status"
      data-testid="payload-shelf-banner"
    >
      <span className="payload-banner__tag mono">{readOnly ? 'SHELF READ-ONLY' : 'PAYLOAD SHELF'}</span>
      <div className="payload-banner__body">
        {counts.target_egress > 0 && (
          <p className="payload-banner__msg">
            <strong>{counts.target_egress}</strong> of {counts.total} tools fetch from the public
            internet <strong>on the target host, at run time</strong>.{' '}
            <strong>{counts.staged}</strong> {counts.staged === 1 ? 'is' : 'are'} staged on this
            SimCore. A default-deny egress policy blocks the rest — the step then runs without its
            tool and the absent detection reads as a miss.
          </p>
        )}
        {counts.target_egress === 0 && counts.staged > 0 && (
          <p className="payload-banner__msg">
            Every tool this catalog can stage is staged. <strong>{counts.staged}</strong> served from
            this SimCore; no target-host egress required for them.
          </p>
        )}
        {counts.unknown > 0 && (
          <p className="payload-banner__msg payload-banner__msg--unknown">
            <strong>{counts.unknown}</strong> adapters have <strong>UNKNOWN</strong> supply state —
            this SimCore did not say whether their artifact is declared or staged. Do not read that
            as either.
          </p>
        )}
        {shelf?.unbound?.length > 0 && (
          <p className="payload-banner__msg payload-banner__msg--unbound" data-testid="payload-unbound">
            <strong>{shelf.unbound.length}</strong> staged {shelf.unbound.length === 1 ? 'artifact is' : 'artifacts are'}{' '}
            <strong>UNBOUND</strong> ({shelf.unbound.map((u) => u.name).join(', ')}) — the bytes are on
            the shelf but no adapter pack declares them, so no scenario can compose them and no
            consumer can be handed them. They count as staged bytes, not as coverage.
          </p>
        )}
        {readOnly && (
          <div className="payload-banner__offline">
            <p className="payload-banner__msg">
              Staging from the console is unavailable — the shelf directory
              (<span className="mono">{shelf?.distDir || 'CORTEXSIM_PAYLOAD_DIST'}</span>) is not
              writable by this SimCore. Stage on a host with internet, then copy it across:
            </p>
            <pre className="payload-banner__recipe mono">{OFFLINE_RECIPE(shelf?.distDir)}</pre>
            <CopyButton value={OFFLINE_RECIPE(shelf?.distDir)} label="Copy offline recipe" variant="btn btn--xs" />
          </div>
        )}
      </div>
      <div className="payload-banner__actions">
        {counts.target_egress > 0 && (
          <button type="button" className="btn btn--xs" onClick={onShowUnstaged}>Show them</button>
        )}
        <button
          type="button"
          className="btn btn--xs"
          aria-expanded={why}
          onClick={() => setWhy((v) => !v)}
        >
          {why ? 'Hide' : 'What does staging do?'}
        </button>
      </div>
      {why && (
        <div className="payload-banner__why" data-testid="payload-why-stage">
          <p>
            Staging pulls the tool onto <strong>this SimCore</strong>, on the DC&apos;s own host,
            where public-internet egress is already accepted. SimCore then serves the bytes to the
            beacon or the pod over the customer&apos;s internal network, so the target never reaches
            github.com.
          </p>
          <p>
            It also makes the artifact <strong>checksummable before it reaches a customer</strong>:
            the digest is resolved here at compose time and travels with the plan, so the consumer
            verifies against a value it carried in rather than one fetched from the same server it
            is trusting.
          </p>
        </div>
      )}
    </div>
  )
}

export const OFFLINE_RECIPE = (distDir) => [
  '# on a host with internet, from a cortex-pov-engine checkout:',
  './scripts/build-payloads.sh          # stages every declared install.artifact',
  '',
  '# then copy the shelf onto the SimCore host and point at it:',
  `scp -r payloads/ dc@simcore-host:${distDir || '/srv/cortexsim/payloads'}`,
  `export CORTEXSIM_PAYLOAD_DIST=${distDir || '/srv/cortexsim/payloads'}`,
  'docker compose restart simcore',
].join('\n')
