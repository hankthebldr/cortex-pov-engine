import React, { useEffect, useRef, useState } from 'react'
import ProvenanceBlock, { CopyButton } from './ProvenanceBlock.jsx'
import { OFFLINE_RECIPE } from './PayloadShelfBanner.jsx'
import { SUPPLY, formatBytes, hostOf } from './supplyState.js'

/**
 * StagePayloadDialog — "the console pulls the public tool onto the DC's host".
 *
 * ONE outbound request, from SimCore, on a host where internet egress is
 * already accepted. Follows ConfirmDialog's conventions (role=dialog,
 * aria-modal, Esc closes, focus restored to the invoking control).
 *
 * TWO THINGS THIS DIALOG WILL NEVER GROW
 * --------------------------------------
 * 1. **A "stage anyway" button on PAYLOAD_PIN_MISMATCH.** A mismatch means
 *    upstream changed under a digest a human set. Blessing that from a console
 *    is how an unreviewed artifact reaches a customer host, and there is no
 *    undo. Repinning is a git commit; the error copy says so. A test asserts
 *    the absence by accessible name.
 * 2. **A byte-progress bar.** `POST /api/shelf/stage` is synchronous and
 *    streams server-side; the browser sees one awaited response. A determinate
 *    percentage here would be a number we made up.
 */
export default function StagePayloadDialog({
  adapter,
  supply,
  job = null,
  writable = null,
  distDir = null,
  onStage = () => {},
  onClose = () => {},
  onCompose = () => {},
}) {
  const [acknowledged, setAcknowledged] = useState(false)
  const primaryRef = useRef(null)
  const previousFocusRef = useRef(null)

  useEffect(() => {
    previousFocusRef.current = document.activeElement
    const t = setTimeout(() => primaryRef.current && primaryRef.current.focus(), 20)
    return () => {
      clearTimeout(t)
      if (previousFocusRef.current?.focus) previousFocusRef.current.focus()
    }
  }, [])

  useEffect(() => {
    const handler = (e) => { if (e.key === 'Escape') { e.preventDefault(); onClose() } }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])

  if (!adapter || !supply) return null

  const name = supply.payloadName || '(undeclared)'
  const host = hostOf(supply.sourceUrl)
  const state = job?.state || 'review'
  const readOnly = writable === false

  return (
    <div
      className="confirm-dialog__backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="stage-dialog-title"
      data-testid="stage-payload-dialog"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="confirm-dialog stage-dialog">
        <h3 id="stage-dialog-title" className="confirm-dialog__title">
          Stage {name} on this SimCore
        </h3>

        {/* ── read-only shelf: no Stage button at all ───────────────────── */}
        {readOnly && state === 'review' && (
          <div className="stage-dialog__body">
            <p className="stage-dialog__lede">
              This SimCore&apos;s payload shelf is read-only
              {distDir ? <> (<span className="mono">{distDir}</span>)</> : null}, so it cannot pull
              the artifact itself. Stage it on a host with internet and copy it across:
            </p>
            <pre className="stage-dialog__recipe mono">{OFFLINE_RECIPE(distDir)}</pre>
            <CopyButton value={OFFLINE_RECIPE(distDir)} label="Copy offline recipe" variant="btn btn--xs" />
          </div>
        )}

        {/* ── review ───────────────────────────────────────────────────── */}
        {!readOnly && state === 'review' && (
          <div className="stage-dialog__body">
            <p className="stage-dialog__lede">
              SimCore will make <strong>one outbound HTTPS request from this host</strong> to{' '}
              <strong className="mono">{host || 'the declared source'}</strong> and write the bytes
              to its shelf. Nothing is sent to the customer&apos;s network.
            </p>
            <ProvenanceBlock supply={supply} adapter={adapter} compact />
            {supply.state === SUPPLY.UNSTAGED && !supply.sha256 && (
              <p className="stage-dialog__warn">
                ⚠ This pack declares no <span className="mono">sha256</span>. The digest recorded
                will be whatever is downloaded right now, so an upstream change would substitute a
                different tool silently. Paste the resulting digest into the pack.
              </p>
            )}
            <label className="stage-dialog__ack">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={() => setAcknowledged((v) => !v)}
                data-testid="stage-license-ack"
              />
              <span>
                I understand <strong className="mono">{name}</strong> is third-party software under{' '}
                <strong>{supply.license || adapter?.upstream?.license || 'an unstated licence'}</strong>,
                that staging places a copy on this SimCore, and that this SimCore will serve it to a
                customer host.
              </span>
            </label>
            {!acknowledged && (
              <ul className="launch-blockers" data-testid="stage-blockers">
                <li className="launch-blockers__item">
                  <span aria-hidden="true">▸</span> Acknowledge the licence — it travels into
                  MANIFEST.json, the inventory endpoint and a POV report a customer&apos;s legal
                  team may read.
                </li>
              </ul>
            )}
          </div>
        )}

        {/* ── in flight ────────────────────────────────────────────────── */}
        {state === 'staging' && (
          <div className="stage-dialog__body" data-testid="stage-in-flight">
            <div className="stage-dialog__progress">
              <span className="stage-dialog__spinner" aria-hidden="true" />
              <span className="mono">
                Fetching from {host || 'upstream'} and verifying — SimCore streams and digests in
                one pass, so there is no intermediate byte count to report.
              </span>
            </div>
            <p className="stage-dialog__note mono">
              The file lands as <span className="mono">.{name}.part</span> and is renamed only after
              the digest is computed and any pin matched. A torn or unverified file can never be
              served.
            </p>
          </div>
        )}

        {/* ── done ─────────────────────────────────────────────────────── */}
        {state === 'done' && (
          <div className="stage-dialog__body" data-testid="stage-done">
            <p className="stage-dialog__ok">
              ✓ Staged. <span className="mono">{job.result?.payload?.name}</span> ·{' '}
              <span className="mono">{formatBytes(job.result?.payload?.size_bytes)}</span>
            </p>
            <div className="stage-dialog__digest mono">
              {job.result?.payload?.sha256}
              <CopyButton value={job.result?.payload?.sha256 || ''} label="copy" />
            </div>
            {(job.result?.warnings || []).map((w) => (
              <p key={w.code} className="stage-dialog__warn" data-testid={`stage-warning-${w.code}`}>
                <span className="mono">{w.code}</span> — {w.detail}
              </p>
            ))}
            {job.result?.pack_snippet && (
              <details className="stage-dialog__snippet">
                <summary>Declare it in the pack (so it survives a clean build)</summary>
                <pre className="mono">{job.result.pack_snippet}</pre>
                <CopyButton value={job.result.pack_snippet} label="Copy pack snippet" variant="btn btn--xs" />
              </details>
            )}
          </div>
        )}

        {/* ── failed ───────────────────────────────────────────────────── */}
        {state === 'failed' && (
          <div className="stage-dialog__body" data-testid="stage-failed">
            <StageError error={job.error} name={name} host={host} distDir={distDir} />
          </div>
        )}

        <div className="confirm-dialog__actions">
          <button type="button" className="btn" onClick={onClose}>
            {state === 'done' ? 'Done' : 'Cancel'}
            <span className="kbd">esc</span>
          </button>

          {!readOnly && state === 'review' && (
            <button
              ref={primaryRef}
              type="button"
              className="btn btn--primary"
              disabled={!acknowledged}
              onClick={() => onStage({ license_acknowledged: true })}
            >
              Stage ▸
            </button>
          )}
          {state === 'failed' && canRetry(job.error) && (
            <button type="button" className="btn btn--primary" onClick={() => onStage({ license_acknowledged: true })}>
              Retry
            </button>
          )}
          {state === 'done' && (
            <button ref={primaryRef} type="button" className="btn btn--primary" onClick={onCompose}>
              Compose into a run ▸
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

/**
 * A pin mismatch and an "already staged" are NOT retryable in place — one needs
 * a git commit, the other an explicit replace decision. Offering Retry there
 * would be a button that cannot work.
 */
function canRetry(error) {
  return !['PAYLOAD_PIN_MISMATCH', 'PAYLOAD_EXISTS', 'SHELF_EGRESS_DISABLED',
    'LICENSE_REQUIRED', 'ADAPTER_HAS_NO_ARTIFACT', 'ADAPTER_NOT_FOUND',
    'BAD_PAYLOAD_URL'].includes(error?.code)
}

/** One message and one next action per code — never a bare status number. */
function StageError({ error, name, host, distDir }) {
  const code = error?.code || 'STAGE_FAILED'
  const copy = ERROR_COPY[code]
  return (
    <>
      <p className="stage-dialog__fail">
        ✗ {copy ? copy.message({ name, host }) : (error?.message || 'Staging failed.')}
        <span className="launch-result__code mono"> [{code}]</span>
      </p>
      {copy?.body && <p className="stage-dialog__note">{copy.body}</p>}
      {error?.detail && <p className="stage-dialog__detail mono">{error.detail}</p>}
      {copy?.offline && (
        <>
          <pre className="stage-dialog__recipe mono">{OFFLINE_RECIPE(distDir)}</pre>
          <CopyButton value={OFFLINE_RECIPE(distDir)} label="Copy offline recipe" variant="btn btn--xs" />
        </>
      )}
    </>
  )
}

export const ERROR_COPY = {
  PAYLOAD_FETCH_FAILED: {
    message: ({ host }) => `SimCore could not fetch from ${host || 'upstream'} — it answered with an error, a redirect, or an HTML page. Nothing was staged.`,
    body: 'A 401, a 403 and a 302 to a captive portal are FAILURES, never staged artifacts: an HTML error page staged under the tool’s name would give a perfect digest, verify flawlessly on the consumer, and run as a no-op.',
    offline: true,
  },
  PAYLOAD_FETCH_TIMEOUT: {
    message: ({ host }) => `SimCore could not reach ${host || 'upstream'}. This host has no route to the internet, or a proxy is holding the connection. Nothing was staged.`,
    offline: true,
  },
  PAYLOAD_STAGE_TIMEOUT: {
    message: () => 'Staging timed out. The artifact was NOT staged and nothing was left on the shelf.',
    offline: true,
  },
  PAYLOAD_PIN_MISMATCH: {
    message: ({ name }) => `Upstream changed. ${name} does not match the digest its pack pins, so it was rejected and nothing was staged.`,
    body: 'There is deliberately no override here. A pin mismatch means upstream changed under a digest a human set; blessing that from a console is how an unreviewed artifact reaches a customer host, and there is no undo. If the change is legitimate, repoint the URL at a release tag and update the pack’s install.artifact.sha256 in git, then rebuild.',
  },
  SHELF_EGRESS_DISABLED: {
    message: () => 'This SimCore is in the air-gapped posture (CORTEXSIM_SHELF_EGRESS=deny) and will not make an outbound request.',
    offline: true,
  },
  PAYLOAD_EXISTS: {
    message: ({ name }) => `${name} is already on the shelf. Silent replacement is refused — it changes the digest under a manifest a DC may already hold.`,
    body: 'Re-stage it deliberately only if you mean to invalidate every composition generated before now.',
  },
  ADAPTER_HAS_NO_ARTIFACT: {
    message: () => 'This adapter declares no install.artifact, so there is nothing to stage.',
    body: 'Add an artifact block to its pack under tools/packs/. The URL is never guessed from install.runtime_install_command — a shell string that silently yields the wrong URL is exactly how staging becomes a no-op.',
  },
  PAYLOAD_TOO_LARGE: {
    message: ({ name }) => `${name} exceeds this SimCore's staging size cap. Nothing was staged.`,
  },
  PAYLOAD_TOKEN_DENIED: {
    message: () => 'This SimCore requires a bearer token on the shelf endpoints, and the console has none.',
  },
  BAD_PAYLOAD_URL: {
    message: () => 'The declared source URL was refused — bad scheme, unresolvable host, or an SSRF guard hit (loopback / link-local / cloud metadata).',
  },
}
