import React from 'react'
import { errorDetailLines, bundleRetryFormat, PUSH_FORMATS } from './useLaunchScenario.js'
import { WINDOWS_BUNDLE_UNPROVEN, WINDOWS_BUNDLE_RESIDUE } from './deployCommands.js'
// .launch-win-note lives with the other Windows notices.
import '../../styles/destinations/deploy-console.css'

/**
 * WindowsBundleNote — what a DC must know before running a .ps1 bundle.
 *
 * The pull path already says "not yet proven on a Windows host". The push path
 * presented a .ps1 as ready, and nothing in the console mentioned that some
 * bundles refuse (exit 2) until CORTEXSIM_ACCEPT_RESIDUE=1 is set. Rendered
 * wherever the console offers or saves a Windows bundle.
 *
 * Props:
 *   inline  — render inside another notice (a span) instead of its own block
 *   testId  — data-testid
 */
export function WindowsBundleNote({ inline = false, testId = undefined }) {
  const Tag = inline ? 'span' : 'p'
  return (
    <Tag className={'launch-win-note' + (inline ? ' launch-win-note--inline' : '')} data-testid={testId}>
      {inline ? ' ' : null}<strong>{WINDOWS_BUNDLE_UNPROVEN}</strong>{' '}{WINDOWS_BUNDLE_RESIDUE}
    </Tag>
  )
}

/**
 * DownloadOutcome — the result of the last push-bundle download, beside the
 * button that asked for it.
 *
 *   success → which file was saved, and for which target SimCore emitted it
 *             (under `auto` that header is the only way to know .sh vs .ps1)
 *   error   → LaunchErrorNotice, plus a one-click retry in the format that can
 *             satisfy the content when there is one (see bundleRetryFormat)
 *
 * Props:
 *   result      — downloadResult from useLaunchScenario (or an equivalent record)
 *   scenario    — for `bundle_targets`, when the server reports it
 *   onDownload  — (format) => void  retry in another format
 *   busy        — a download is in flight
 *   testId      — data-testid prefix
 *   launch      — convenience: a useLaunchScenario() return value supplying the above
 */
export function DownloadOutcome({
  launch = null,
  result = launch?.downloadResult ?? null,
  scenario = null,
  onDownload = launch?.downloadPushBundle ?? null,
  busy = launch?.downloading ?? false,
  testId = 'download-result',
}) {
  if (!result) return null
  if (result.status === 'success') {
    // Saved is not the same as known to run. A .ps1 is authored and
    // parse-checked; none has executed on a Windows host.
    const isWindowsBundle = result.target === 'windows' || /\.ps1$/i.test(String(result.filename || ''))
    return (
      <div className="launch-result launch-result--success" role="status" data-testid={testId}>
        Saved <span className="mono">{result.filename}</span>
        {result.target && <> · SimCore emitted it for <span className="mono">{result.target}</span></>}
        {isWindowsBundle && <WindowsBundleNote inline />}
      </div>
    )
  }
  const retry = bundleRetryFormat(result, scenario)
  const retryLabel = retry ? (PUSH_FORMATS.find((f) => f.value === retry)?.short || retry) : null
  return (
    <LaunchErrorNotice result={result} testId={testId}>
      {retry && onDownload && (
        <button
          type="button"
          className="btn btn--xs launch-result__retry"
          disabled={busy}
          onClick={() => onDownload(retry)}
        >
          Download as {retryLabel}
        </button>
      )}
    </LaunchErrorNotice>
  )
}

/**
 * LaunchErrorNotice — a refused launch or download, rendered where the operator
 * clicked: message, code, and every line of the structured detail.
 *
 * One component for both because SimCore refuses both with the same envelope,
 * and the console used to drop the part that names what to fix: a download 409
 * reached a no-op `onError`, and a launch 409 kept its detail only when it was
 * a string — so PULL_TARGET_UNSATISFIABLE's `unresolved[]` and
 * TARGET_AGENT_NOT_ENROLLED's `referencing_steps` never reached the screen.
 *
 * `role="alert"`: it is the answer to the click the operator just made.
 *
 * Props:
 *   result    — {status:'error', message, code, detail} (anything else renders nothing)
 *   testId    — data-testid
 *   className — container class (defaults to the launch-result error styling)
 *   children  — actions offered alongside (e.g. "Download as PowerShell")
 */
export default function LaunchErrorNotice({
  result,
  testId = undefined,
  className = 'launch-result launch-result--error',
  children = null,
}) {
  if (!result || result.status !== 'error') return null
  const message = result.message || 'Request failed'
  const lines = errorDetailLines(result.detail)
  const showCode = result.code && !String(message).includes(result.code)
  return (
    <div className={className} role="alert" data-testid={testId}>
      <div className="launch-result__message">
        {message}
        {showCode && <span className="launch-result__code mono"> [{result.code}]</span>}
      </div>
      {lines.length > 0 && (
        <ul className="launch-result__detail mono">
          {lines.map((line, i) => <li key={`${i}-${line}`}>{line}</li>)}
        </ul>
      )}
      {children}
    </div>
  )
}
