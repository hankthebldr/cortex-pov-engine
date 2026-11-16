/**
 * RunNotices — the one line on a run's page for what SimCore said about the
 * launch (runNotices.js): the agent was offline, it lacks an interpreter a
 * step needs, its beacon does not run cleanup or stream output.
 *
 * One notice is one line, with SimCore's own sentence in its tooltip. Several
 * are still one line ("3 notices" and the first), opened in place to one line
 * each. Nothing to say draws nothing.
 */
import React from 'react'
import { StatusIcon } from './kit/index.js'

void React

export default function RunNotices({ notices = [] }) {
  if (!notices.length) return null
  const [first] = notices
  if (notices.length === 1) {
    return (
      <div className="run-notice" role="status" data-testid="run-notice">
        <StatusIcon status="degraded" word="Notice" />
        <span className="run-notice__line" title={first.detail}>{first.line}</span>
      </div>
    )
  }
  return (
    <div className="run-notice" role="status" data-testid="run-notice">
      <details className="run-notice__more">
        <summary>
          <StatusIcon status="degraded" word={`${notices.length} notices`} />
          <span className="run-notice__line run-notice__first">{first.line}</span>
          <span className="run-notice__toggle run-notice__toggle--show">Show all</span>
          <span className="run-notice__toggle run-notice__toggle--hide">Hide</span>
        </summary>
        <ul className="run-notice__list">
          {notices.map((n) => (
            <li key={n.code} className="run-notice__line" title={n.detail}>{n.line}</li>
          ))}
        </ul>
      </details>
    </div>
  )
}
