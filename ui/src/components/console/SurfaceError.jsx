import React from 'react'

/**
 * SurfaceError — the explicit error states a destination renders.
 *
 * A destination that fails must never render an empty <main>: mid-demo, a
 * black rectangle under a nav bar gives the operator nothing to say and no
 * way to recover. This states what failed, quotes the structured `code`
 * SimCore returned, and offers a real recovery action.
 *
 * Two distinct failure shapes reach here and are NOT interchangeable:
 *
 *  - An ordinary render throw inside an already-loaded surface (a bug, a
 *    bad response shape). "Retry" clearing local state and re-rendering
 *    the same component is a real fix for anything transient.
 *  - A lazy DESTINATION CHUNK failing to `import()` — most often because
 *    this tab's index.html still points at hashed chunk filenames a
 *    redeployed SimCore no longer serves (every destination 404s), or a
 *    one-off network blip on a single chunk fetch. React caches a
 *    `lazy()` component's outcome — success OR rejection — forever, so
 *    re-rendering the SAME lazy object after a rejection just replays the
 *    identical cached error: the old single "Retry" button was a dead end
 *    here, not a fix. `error.isChunkLoadError` (set by
 *    `destinations.jsx::makeLazySurface`, the only place a rejected
 *    `factory()` — i.e. the import itself failing — can originate; a
 *    render error from inside the resolved component never carries the
 *    tag) is what tells the two apart, deliberately not a browser-specific
 *    string match against "Failed to fetch dynamically imported module"
 *    (Chrome) / "error loading dynamically imported module" (Firefox) /
 *    "Importing a module script failed" (Safari).
 *
 * Props:
 *   title         — what could not be loaded ("Coverage")
 *   error         — Error | string; `error.code` is surfaced when present;
 *                   `error.isChunkLoadError` selects the chunk-load copy
 *   onRetry       — () => void   plain-throw retry; omit to hide
 *   onRetryImport — () => void   chunk-load "Try again" (re-imports); omit to hide
 *   onReload      — () => void   chunk-load "Reload app" (full page reload)
 *   children      — optional extra guidance (a link to another destination, etc.)
 */
export default function SurfaceError({
  title = 'This view', error = null, onRetry = null, onRetryImport = null, onReload = null, children = null,
}) {
  const chunkLoad = Boolean(error && error.isChunkLoadError)
  const message = (error && (error.message || String(error))) || 'unknown error'
  const code = error && error.code ? error.code : null

  return (
    <div className="surface-error" role="alert" data-testid="surface-error">
      <div className="surface-error__glyph" aria-hidden="true">⚠</div>
      <h2 className="surface-error__title">
        {chunkLoad ? `${title} is out of date` : `${title} could not load`}
      </h2>
      <p className="surface-error__msg mono">
        {message}
        {code && <span className="surface-error__code"> [{code}]</span>}
      </p>
      <p className="surface-error__hint">
        {chunkLoad
          ? 'A newer build was deployed after this tab loaded, so this part of the console no longer matches what the server has. Reload the app to pick up the new build — the tab will not fix itself on its own.'
          : 'SimCore may be restarting, or a proxy between this console and the API is interfering. Nothing has been lost — retry when it is back.'}
      </p>
      {children}
      <div className="surface-error__actions" style={{ display: 'flex', gap: 8 }}>
        {chunkLoad && onRetryImport && (
          <button type="button" className="btn" onClick={onRetryImport}>
            ↻ Try again
          </button>
        )}
        {chunkLoad && onReload && (
          <button type="button" className="btn btn--primary" onClick={onReload}>
            ⟳ Reload app
          </button>
        )}
        {!chunkLoad && onRetry && (
          <button type="button" className="btn btn--primary" onClick={onRetry}>
            ↻ Retry
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * SurfaceBoundary — last-resort catch so one throwing surface cannot blank the
 * console. React unmounts the whole tree on an uncaught render error, which is
 * exactly the empty-<main> failure mode this exists to prevent.
 *
 * `resetKey` (the current destination id, or a retry counter — see
 * `makeLazySurface`) re-arms the boundary when it changes, so a broken
 * surface does not poison the ones the operator moves to next.
 *
 * `onRetryImport`, when the caught error is chunk-load-shaped, is threaded
 * through to a "Try again" action instead of the plain "Retry" — plain
 * retry only clears local state, which does nothing for a `lazy()` whose
 * cached rejection React will replay forever; the caller must supply a way
 * to construct a genuinely NEW import attempt. `onReload` defaults to a
 * full `window.location.reload()` when the caller doesn't override it, so
 * even a boundary that has no retry wiring (this repo's single outer one in
 * AppConsole.jsx) still gives a chunk-load failure a working way out.
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

  handleRetry = () => {
    this.setState({ error: null })
  }

  handleRetryImport = () => {
    // The parent owns re-creating the lazy() component (a fresh `attempt`
    // triggers `useMemo` to build a brand-new one, which re-invokes the
    // import factory); clearing local error state here just makes sure a
    // caller that doesn't also change `resetKey` still re-renders children.
    this.setState({ error: null })
    if (this.props.onRetryImport) this.props.onRetryImport()
  }

  render() {
    if (this.state.error) {
      const chunkLoad = Boolean(this.state.error.isChunkLoadError)
      return (
        <SurfaceError
          title={this.props.title || 'This view'}
          error={this.state.error}
          onRetry={chunkLoad ? null : this.handleRetry}
          onRetryImport={chunkLoad && this.props.onRetryImport ? this.handleRetryImport : null}
          onReload={chunkLoad ? (this.props.onReload || (() => window.location.reload())) : null}
        />
      )
    }
    return this.props.children
  }
}
