import React from 'react'

/**
 * SurfaceError — the ONE explicit error state a destination renders.
 *
 * A destination that fails must never render an empty <main>: mid-demo, a
 * black rectangle under a nav bar gives the operator nothing to say and no way
 * to recover. This states what failed, quotes the structured `code` SimCore
 * returned, and offers a retry.
 *
 * Props:
 *   title    — what could not be loaded ("Coverage")
 *   error    — Error | string; `error.code` is surfaced when present
 *   onRetry  — () => void   omit to hide the retry action
 *   children — optional extra guidance (a link to another destination, etc.)
 */
export default function SurfaceError({ title = 'This view', error = null, onRetry = null, children = null }) {
  const message = (error && (error.message || String(error))) || 'unknown error'
  const code = error && error.code ? error.code : null

  return (
    <div className="surface-error" role="alert" data-testid="surface-error">
      <div className="surface-error__glyph" aria-hidden="true">⚠</div>
      <h2 className="surface-error__title">{title} could not load</h2>
      <p className="surface-error__msg mono">
        {message}
        {code && <span className="surface-error__code"> [{code}]</span>}
      </p>
      <p className="surface-error__hint">
        SimCore may be restarting, or a proxy between this console and the API is
        interfering. Nothing has been lost — retry when it is back.
      </p>
      {children}
      {onRetry && (
        <button type="button" className="btn btn--primary" onClick={onRetry}>
          ↻ Retry
        </button>
      )}
    </div>
  )
}

/**
 * SurfaceBoundary — last-resort catch so one throwing surface cannot blank the
 * console. React unmounts the whole tree on an uncaught render error, which is
 * exactly the empty-<main> failure mode this exists to prevent.
 *
 * `resetKey` (the current destination id) re-arms the boundary on navigation,
 * so a broken surface does not poison the ones the operator moves to next.
 */
export class SurfaceBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidUpdate(prevProps) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  render() {
    if (this.state.error) {
      return (
        <SurfaceError
          title={this.props.title || 'This view'}
          error={this.state.error}
          onRetry={() => this.setState({ error: null })}
        />
      )
    }
    return this.props.children
  }
}
