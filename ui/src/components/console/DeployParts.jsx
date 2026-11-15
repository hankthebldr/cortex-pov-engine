import React, { useCallback, useEffect, useRef, useState } from 'react'
import { agentBinaryUrl, agentInstallUrl, getAgentBinarySha256, getInstallAttempts } from '../../api/client.js'
import { parseServerTime } from '../../api/time.js'
import {
  CONTINUED_NOTE,
  WINDOWS_BEACON_FILE,
  WINDOWS_BEACON_PATH,
  WINDOWS_CAVEATS,
  WINDOWS_INSTALLER_FILE,
  archLabel,
  windowsPrestageRunLine,
  windowsPrestageVerifyLine,
  attemptKind,
  attemptRole,
  copyFailureMessage,
  copyToClipboard,
  normaliseServerUrl,
  selectNodeText,
  serverUrlProblem,
} from './deployCommands.js'
import '../../styles/destinations/deploy-console.css'

/**
 * DeployParts — the pieces the Agents deploy modal and the Get started agent
 * step share: install-telemetry polling, the "what did the installer say"
 * verdict, the recent-attempts list, honest Copy feedback, and the Windows
 * notices. One implementation, so the two front doors cannot disagree.
 */

export const ATTEMPTS_POLL_MS = 5000

/**
 * Poll GET /api/agents/install/attempts while `active`.
 *
 * `refreshKey` re-fetches at once when it changes (a token was just minted), so
 * the DC does not wait a full interval for the first verdict on it.
 *
 * @returns {{attempts: Object[]|undefined, error: string|null, unsupported: boolean}}
 *   `attempts` is undefined until the first answer; `unsupported` means the
 *   SimCore has no such endpoint (404) — a gap, not "no attempts".
 */
export function useInstallAttempts(active, { pollMs = ATTEMPTS_POLL_MS, refreshKey = null } = {}) {
  const [state, setState] = useState({ attempts: undefined, error: null, unsupported: false })
  useEffect(() => {
    if (!active) return undefined
    let cancelled = false
    const load = () => {
      getInstallAttempts()
        .then((list) => {
          if (cancelled) return
          if (list === null) setState({ attempts: [], error: null, unsupported: true })
          else setState({ attempts: Array.isArray(list) ? list : [], error: null, unsupported: false })
        })
        .catch((err) => {
          if (cancelled) return
          setState((s) => ({ ...s, error: (err && err.message) || 'could not read install attempts' }))
        })
    }
    load()
    const t = setInterval(load, pollMs)
    return () => { cancelled = true; clearInterval(t) }
  }, [active, pollMs, refreshKey])
  return state
}

/**
 * Copy with feedback that tells the truth: "copied" only after the clipboard
 * (or the execCommand fallback) said yes; on failure the snippet is selected
 * and the message says why. `feedback.message` is meant for an aria-live node.
 */
export function useCopyFeedback({ resetMs = 1800 } = {}) {
  const [feedback, setFeedback] = useState(null) // { key, ok, message }
  const timer = useRef(null)
  useEffect(() => () => clearTimeout(timer.current), [])
  const copy = useCallback(async (key, text, selectEl = null, what = 'text') => {
    const result = await copyToClipboard(text)
    clearTimeout(timer.current)
    if (result === 'copied') {
      setFeedback({ key, ok: true, message: `Copied the ${what}.` })
      timer.current = setTimeout(() => setFeedback((cur) => (cur && cur.key === key ? null : cur)), resetMs)
    } else {
      selectNodeText(selectEl && 'current' in selectEl ? selectEl.current : selectEl)
      setFeedback({ key, ok: false, message: copyFailureMessage() })
    }
    return result
  }, [resetMs])
  const isCopied = useCallback((key) => !!(feedback && feedback.ok && feedback.key === key), [feedback])
  return { feedback, copy, isCopied }
}

/** The aria-live node every Copy button on a surface reports into. */
export function CopyLive({ feedback, testId, className = '' }) {
  return (
    <span
      className={'deploy-copy-live' + (feedback && !feedback.ok ? ' deploy-copy-live--fail' : '') + (className ? ` ${className}` : '')}
      role="status"
      aria-live="polite"
      data-testid={testId}
    >
      {feedback ? feedback.message : ''}
    </span>
  )
}

function relAgo(iso) {
  const ms = Date.now() - parseServerTime(iso)
  if (!Number.isFinite(ms)) return 'time unknown'
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function where(a) {
  const plat = [a.os, a.arch].filter(Boolean).join('/')
  return [a.hostname, plat].filter(Boolean).join(' · ')
}

/** `10:00:07 UTC` — a report time that does not change between renders. */
function reportedAt(iso) {
  const ms = parseServerTime(iso)
  return Number.isFinite(ms) ? `${new Date(ms).toISOString().slice(11, 19)} UTC` : 'time unknown'
}

const KIND_CLASS = { ok: 'ok', continued: 'warn', failed: 'fail' }
const KIND_WORD = { ok: 'ok', continued: 'warning', failed: 'stopped' }
const KIND_LEAD = {
  ok: 'The installer reported ',
  continued: 'The installer carried on past a warning: ',
  failed: 'The installer stopped: ',
}

/**
 * The newest attempt reported after this token was minted: role=alert when the
 * installer stopped, role=status when it reported and carried on. Renders
 * nothing when nothing has reported yet — the caller keeps its "waiting" line.
 *
 * Stop or carry-on is read from the whole row (attemptKind(attempt)), not its
 * code alone: SERVICE_START_FAILED ends the Windows installer and only warns
 * from the POSIX one.
 *
 * The live node holds only text that is fixed for one attempt. The attempts
 * poll re-renders this every 5 s, and role=alert is assertive and atomic: a
 * ticking "12s ago" inside it made a screen reader re-read the whole failure
 * every five seconds. The ticking age sits outside, aria-hidden; the node
 * carries a fixed report time instead, and is keyed on the attempt so a NEW
 * attempt is a new node and is announced once.
 */
export function LatestAttempt({ attempt, testId = 'deploy-attempt-latest' }) {
  if (!attempt) return null
  const kind = attemptKind(attempt)
  const code = String(attempt.code ?? '').trim().toUpperCase()
  const next = kind === 'failed'
    ? 'The target printed the fix line next to this code; re-run once it is fixed.'
    : kind === 'continued'
      ? CONTINUED_NOTE[code]
      : 'The agent counts as online only after the beacon polls.'
  return (
    <div className={`install-attempt install-attempt--${KIND_CLASS[kind]}`}>
      <div
        key={`${attempt.reported_at || ''}|${attempt.stage || ''}|${attempt.code || ''}`}
        className="install-attempt__live"
        role={attemptRole(attempt)}
        data-testid={testId}
      >
        <span className="install-attempt__lead">{KIND_LEAD[kind]}</span>
        <code className="mono">{attempt.stage || '?'} · {attempt.code || '(no code)'}</code>
        {attempt.detail ? <span className="install-attempt__detail"> — {attempt.detail}</span> : null}
        <span className="install-attempt__meta mono">
          {' '}({[where(attempt), `reported ${reportedAt(attempt.reported_at)}`].filter(Boolean).join(' · ')})
        </span>
        <span className="install-attempt__next">{' '}{next}</span>
      </div>
      <span className="install-attempt__ago mono" aria-hidden="true" data-testid={`${testId}-ago`}>
        {relAgo(attempt.reported_at)}
      </span>
    </div>
  )
}

/** Recent attempts, unfiltered — including ones from before this token. */
export function InstallAttemptsList({ state, limit = 5, testId = 'deploy-attempts' }) {
  const { attempts, error, unsupported } = state
  let body
  if (unsupported) {
    body = (
      <p className="install-attempts__empty">
        This SimCore does not report install attempts (<code className="mono">GET /api/agents/install/attempts</code> is
        absent), so an installer failure cannot be shown here. Read it on the target&apos;s console.
      </p>
    )
  } else if (error) {
    body = <p className="install-attempts__empty install-attempts__empty--err">Could not read install attempts: {error}</p>
  } else if (attempts === undefined) {
    body = <p className="install-attempts__empty">Reading install attempts…</p>
  } else if (attempts.length === 0) {
    body = <p className="install-attempts__empty">No installer has reported to this SimCore since it last started.</p>
  } else {
    body = (
      <ul className="install-attempts__list" data-testid={`${testId}-list`}>
        {attempts.slice(0, limit).map((a, i) => (
          <li key={`${a.reported_at || ''}-${i}`} className={`install-attempts__row install-attempts__row--${KIND_CLASS[attemptKind(a)]}`}>
            {/* The word, not only the colour, says which rows stopped an install. */}
            <span className="install-attempts__kind">{KIND_WORD[attemptKind(a)]}</span>{' '}
            <span className="mono">{a.stage || '?'} · {a.code || '(no code)'}</span>
            {a.detail ? <span className="install-attempts__detail"> — {a.detail}</span> : null}
            <span className="install-attempts__meta mono"> {[where(a), relAgo(a.reported_at)].filter(Boolean).join(' · ')}</span>
          </li>
        ))}
      </ul>
    )
  }
  return (
    <section className="install-attempts" data-testid={testId} aria-label="Recent install attempts">
      <div className="install-attempts__head">Recent install attempts</div>
      <p className="install-attempts__note">
        What each installer reported, newest first. In-memory on SimCore (last 100) — the list resets when
        SimCore restarts. Refreshes every {Math.round(ATTEMPTS_POLL_MS / 1000)} s while this is open.
      </p>
      {body}
    </section>
  )
}

/**
 * A Copy button that says WHAT it copies. A surface with several buttons all
 * named "Copy" is a list of identical controls to a screen reader. The visible
 * text stays short; the accessible name carries the object. The result is
 * announced by the surface's one CopyLive node, not by this button.
 */
export function CopyButton({ copier, id, text, selectRef, what, name = what, className = 'btn btn--xs' }) {
  const copied = copier.isCopied(id)
  return (
    <button
      type="button"
      className={className}
      aria-label={`${copied ? 'Copied' : 'Copy'} ${name}`}
      onClick={() => copier.copy(id, text, selectRef, what)}
    >{copied ? '✓ copied' : 'Copy'}</button>
  )
}

/**
 * WindowsNoCradle — install on Windows without piping a download into `iex`.
 *
 * The one-liner is `iwr … | iex`: download-and-execute in one line, on
 * endpoints that run the prevention product being demonstrated. This brings the
 * installer AND the beacon as files. The DC checks the beacon's hash against
 * the digest SimCore serves, then runs the installer with $env:CORTEXSIM_BIN
 * set to that file — an input the installer already honours, and re-checks.
 *
 * Uses only routes SimCore serves today (install, binary, binary/sha256).
 * It does not claim to get past any prevention profile: neither path has run
 * on a host with XDR or Defender in prevent mode, and this download-then-run
 * path has not run on Windows at all (the iwr | iex one-liner has run only in CI).
 *
 * `server` is the address the TARGET reaches SimCore at — the one the
 * one-liner beside this is built from. The installer link is fetched by this
 * BROWSER, so without `?server=` SimCore would bake the address the console
 * was opened at (its Host header): with the console on an SSH tunnel that is
 * localhost, and the saved .ps1 then fails at enrol on the target with
 * SERVER_UNREACHABLE, reporting nothing back. So the link always names the
 * address, and an address SimCore would refuse (400 BAD_SERVER) gets no link
 * at all — the browser would save the refusal as the .ps1. The beacon and its
 * digest are the same bytes whatever the address, so those stay relative.
 *
 * Props: `token` (the minted token value) · `copier` (useCopyFeedback()) ·
 *        `server` (required: the address baked into the installer) ·
 *        `testIdPrefix` · `buttonClassName`
 */
export function WindowsNoCradle({ token, copier, server, testIdPrefix = 'deploy', buttonClassName = 'btn btn--xs' }) {
  const baked = normaliseServerUrl(server)
  const serverProblem = serverUrlProblem(baked)
  const [digest, setDigest] = useState({ sha256: null, error: null })
  const verifyRef = useRef(null)
  const runRef = useRef(null)
  useEffect(() => {
    let cancelled = false
    getAgentBinarySha256({ os: 'windows', arch: 'amd64' })
      .then((sha256) => { if (!cancelled) setDigest({ sha256, error: null }) })
      .catch((err) => {
        if (cancelled) return
        const msg = (err && err.message) || 'could not read the digest'
        setDigest({ sha256: null, error: err && err.code && !msg.includes(err.code) ? `${msg} [${err.code}]` : msg })
      })
    return () => { cancelled = true }
  }, [])

  const p = `${testIdPrefix}-nocradle`
  const verifyLine = digest.sha256 ? windowsPrestageVerifyLine({ sha256: digest.sha256 }) : null
  const runLine = windowsPrestageRunLine({ token })
  return (
    <section className="nocradle" data-testid={p} aria-label="Install without iwr | iex">
      <div className="nocradle__head">Or without <code className="mono">iwr | iex</code> — download, verify, run</div>
      <p className="nocradle__lede">
        Brings the installer and the beacon to the host as files you have checked, instead of piping a download
        into <code className="mono">iex</code>.
      </p>
      <ol className="nocradle__steps">
        <li>
          Download both here:{' '}
          {serverProblem ? (
            <span className="nocradle__err" role="status" data-testid={`${p}-installer-unavailable`}>
              no installer link — SimCore will not write <code className="mono">{baked || '(nothing)'}</code> into
              an installer as the address the beacon calls (<code className="mono">400 BAD_SERVER</code>:{' '}
              {serverProblem}). Use the form <code className="mono">http://&lt;ip-address&gt;:8888</code>.
            </span>
          ) : (
            <a className="linklike" href={agentInstallUrl({ os: 'windows', server: baked })} download={WINDOWS_INSTALLER_FILE}>↓ installer (.ps1)</a>
          )}{' · '}
          <a className="linklike" href={agentBinaryUrl({ os: 'windows', arch: 'amd64' })} download={WINDOWS_BEACON_FILE}>↓ beacon (.exe)</a>
          <br />
          {digest.sha256 ? (
            <>SHA-256 of the beacon SimCore serves: <code className="mono" data-testid={`${p}-sha256`}>{digest.sha256}</code></>
          ) : digest.error ? (
            <span className="nocradle__err" role="status" data-testid={`${p}-sha256-error`}>
              SimCore gave no digest for the Windows beacon ({digest.error}), so there is nothing to check the file
              against here.
            </span>
          ) : (
            <span className="nocradle__muted">reading the beacon&apos;s SHA-256…</span>
          )}
        </li>
        <li>Copy both files into one folder on the target.</li>
        {verifyLine && (
          <li>
            There, in Windows PowerShell as Administrator, check the beacon — this must print <code className="mono">True</code>:
            <div className="deploy-snippet">
              <code data-testid={`${p}-verify`} ref={verifyRef}>{verifyLine}</code>
              <CopyButton copier={copier} id={`${p}-verify`} text={verifyLine} selectRef={verifyRef} what="hash check" className={buttonClassName} />
            </div>
          </li>
        )}
        <li>
          {verifyLine ? 'Then run the installer on that beacon:' : 'In Windows PowerShell as Administrator, in that folder, run the installer on that beacon:'}
          <div className="deploy-snippet">
            <code data-testid={`${p}-run`} ref={runRef}>{runLine}</code>
            <CopyButton
              copier={copier}
              id={`${p}-run`}
              text={runLine}
              selectRef={runRef}
              what="run line"
              name="run line for the downloaded installer and beacon"
              className={buttonClassName}
            />
          </div>
          {serverProblem ? (
            <>The installer still contacts SimCore, to enrol with the token and to compare the beacon with the same digest.</>
          ) : (
            <span data-testid={`${p}-server`}>
              The installer still contacts SimCore at <code className="mono">{baked}</code> from the target, to enrol
              with the token and to compare the beacon with the same digest. That address is written into the
              installer when you download it, so it must be one the target can reach.
            </span>
          )}
        </li>
      </ol>
      <p className="nocradle__note">
        <b>Authored, not yet run on a Windows host.</b> Whether an endpoint prevention profile (Cortex XDR,
        Defender) allows the unsigned beacon or the new service has not been tested, on this path or the one-liner.
        Ask for an allow for <code className="mono">{WINDOWS_BEACON_PATH}</code> by that SHA-256 before the session.
      </p>
    </section>
  )
}

/**
 * A Linux or macOS target this SimCore cannot fully serve a prebuilt beacon
 * for. `missing`: no architecture is on the shelf. `partial`: some are — an
 * amd64-only shelf serves an Intel Mac and not an Apple-silicon one, and the
 * installer on the unserved host can only build from source. Renders nothing
 * when every architecture is served, or for Windows (which has its own notice).
 */
export function PosixBeaconGap({ ship, className = 'deploy-warn', testIdPrefix = 'deploy' }) {
  if (!ship || ship.os === 'windows') return null
  if (ship.state !== 'missing' && ship.state !== 'partial') return null
  const osName = ship.os === 'darwin' ? 'macOS' : 'Linux'
  const named = (list) => list.map((t) => `${t} (${archLabel(t)})`).join(', ')
  const sourceBuild = (
    <>
      the installer can only build from source — that needs Go and a CortexSim checkout on the host
      (<code>CORTEXSIM_SRC</code>). Run <code>make agent-dist</code> on the POVengine host to fix that.
    </>
  )
  if (ship.state === 'missing') {
    return (
      <p className={className} role="status" data-testid={`${testIdPrefix}-posix-missing`}>
        This POVengine has no prebuilt {osName} agent ({ship.targets.join(', ')}), so {sourceBuild}
      </p>
    )
  }
  return (
    <p className={className} role="status" data-testid={`${testIdPrefix}-posix-partial`}>
      This POVengine has no prebuilt {osName} agent for {named(ship.missingTargets)}. On a host of that
      architecture {sourceBuild}{' '}
      {ship.presentTargets.length > 0 && <>Hosts on {named(ship.presentTargets)} are served.</>}
    </p>
  )
}

/** This SimCore cannot serve the Windows beacon — a packaging gap, not a missing feature. */
export function WindowsBeaconMissing({ className = 'deploy-warn', testId = 'deploy-windows-missing' }) {
  return (
    <p className={className} role="status" data-testid={testId}>
      ⚠ This SimCore was built without the windows beacon — rebuild with <code className="mono">make agent-dist</code>{' '}
      (or rebuild the image; its agent-builder stage bakes the beacon in). Until then the PowerShell installer will
      refuse (<code className="mono">WINDOWS_AGENT_UNAVAILABLE</code>) before it enrols anything. Use the offline
      push bundle for Windows targets meanwhile.
    </p>
  )
}

/** The true Windows caveats, plus "cannot confirm" when SimCore did not say what it ships. */
export function WindowsCaveats({ ship, testId = 'deploy-windows-caveats' }) {
  return (
    <div className="win-caveats" role="note" data-testid={testId}>
      <div className="win-caveats__head">Windows — read before the session</div>
      {ship && ship.state === 'unknown' && (
        <p className="win-caveats__unknown">
          This console cannot confirm that SimCore serves a windows/amd64 beacon ({ship.reason}). If it does not,
          the installer refuses with <code className="mono">WINDOWS_AGENT_UNAVAILABLE</code>.
        </p>
      )}
      <ul className="win-caveats__list">
        {WINDOWS_CAVEATS.map((c) => (
          <li key={c.id} data-caveat={c.id}><b>{c.title}</b> {c.body}</li>
        ))}
      </ul>
    </div>
  )
}
