import React, { useCallback, useEffect, useRef, useState } from 'react'
import { getInstallAttempts } from '../../api/client.js'
import { parseServerTime } from '../../api/time.js'
import {
  WINDOWS_CAVEATS,
  attemptRole,
  copyFailureMessage,
  copyToClipboard,
  selectNodeText,
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

/**
 * The newest attempt reported after this token was minted: role=alert for a
 * failure, role=status for OK / DEGRADED_NO_SUPERVISOR. Renders nothing when
 * nothing has reported yet — the caller keeps its "waiting" line then.
 */
export function LatestAttempt({ attempt, testId = 'deploy-attempt-latest' }) {
  if (!attempt) return null
  const role = attemptRole(attempt.code)
  const failed = role === 'alert'
  return (
    <div className={`install-attempt install-attempt--${failed ? 'fail' : 'ok'}`} role={role} data-testid={testId}>
      <span className="install-attempt__lead">
        {failed ? 'The installer stopped: ' : 'The installer reported '}
      </span>
      <code className="mono">{attempt.stage || '?'} · {attempt.code || '(no code)'}</code>
      {attempt.detail ? <span className="install-attempt__detail"> — {attempt.detail}</span> : null}
      <span className="install-attempt__meta mono">
        {' '}({[where(attempt), relAgo(attempt.reported_at)].filter(Boolean).join(' · ')})
      </span>
      <span className="install-attempt__next">
        {failed
          ? ' The target printed the fix line next to this code; re-run once it is fixed.'
          : ' The agent counts as online only after the beacon polls.'}
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
          <li key={`${a.reported_at || ''}-${i}`} className={`install-attempts__row install-attempts__row--${attemptRole(a.code) === 'alert' ? 'fail' : 'ok'}`}>
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
