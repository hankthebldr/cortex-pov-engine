import { useCallback, useEffect, useRef, useState } from 'react'
import { canStep, recordLocation, startHistory } from './navHistory.js'

/** The hash for a destination and its params: `#/runs?run=r-42&tab=live`. */
export function hashFor(destination, params) {
  const keys = params ? Object.keys(params).filter((k) => params[k] != null && params[k] !== '') : []
  if (!keys.length) return `#/${destination}`
  const sp = new URLSearchParams()
  keys.forEach((k) => sp.set(k, String(params[k])))
  return `#/${destination}?${sp.toString()}`
}

/**
 * useConsoleRouter — a tiny, zero-dependency hash router.
 *
 * No react-router. The URL hash encodes the current destination plus any
 * surface-local query params:
 *
 *   #/library
 *   #/runs?run=r-42&tab=live
 *
 * Exposes { destination, params, navigate, setParams } and stays in sync with
 * browser back/forward via `hashchange`. StrictMode-safe: the effect only
 * subscribes to the event (no double-fire side effects), and navigate is a
 * pure `location.hash` write that the listener reflects back into state.
 *
 * It also keeps the console's own history (design C9, `navHistory.js`):
 * every page, tab, run and deep dive the URL names, which the header's Back /
 * Forward walk through `back()` / `forward()` and report as `canBack` /
 * `canForward`. It is the router's list, not the browser's, because
 * `setParams` rewrites the current browser entry rather than adding one.
 *
 * @param {Object}  opts
 * @param {string}  opts.defaultDestination  fallback when the hash is empty/unknown
 * @param {(id:string)=>boolean} [opts.isValid]  guard for unknown destinations
 * @param {(id:string)=>boolean} [opts.isRedirect]  a destination that only
 *        forwards elsewhere; it never stays in the history
 */
export default function useConsoleRouter({ defaultDestination = 'library', isValid, isRedirect } = {}) {
  const parse = useCallback(() => {
    const raw = typeof window !== 'undefined' ? window.location.hash : ''
    // strip leading '#', '#/', '/'
    const cleaned = raw.replace(/^#/, '').replace(/^\//, '')
    const [pathPart, queryPart] = cleaned.split('?')
    let dest = (pathPart || '').split('/')[0] || defaultDestination
    if (isValid && !isValid(dest)) dest = defaultDestination
    const params = {}
    if (queryPart) {
      const sp = new URLSearchParams(queryPart)
      for (const [k, v] of sp.entries()) params[k] = v
    }
    return { destination: dest, params }
  }, [defaultDestination, isValid])

  const [state, setState] = useState(() => {
    const loc = parse()
    return { ...loc, history: startHistory(loc) }
  })

  const isRedirectRef = useRef(isRedirect)
  useEffect(() => { isRedirectRef.current = isRedirect }, [isRedirect])

  // The router's next state: `loc`, recorded in its history.
  const record = useCallback((prev, loc, traversal = false) => ({
    ...loc,
    history: recordLocation(prev.history, loc, {
      isRedirect: (id) => !!(isRedirectRef.current && isRedirectRef.current(id)),
      traversal,
    }),
  }), [])

  // Counts navigate() calls whose hashchange has not arrived yet, so the
  // change each one causes reads as a step forward, not as the browser's own
  // Back / Forward.
  const pendingRef = useRef(0)

  useEffect(() => {
    const onHashChange = () => {
      const traversal = pendingRef.current === 0
      if (!traversal) pendingRef.current -= 1
      setState((prev) => record(prev, parse(), traversal))
    }
    window.addEventListener('hashchange', onHashChange)
    // Normalize an empty hash to the default destination on mount so the URL
    // is always meaningful (and back/forward has something to return to).
    if (!window.location.hash) {
      window.location.replace(`#/${defaultDestination}`)
    } else {
      setState((prev) => record(prev, parse(), true))
    }
    return () => window.removeEventListener('hashchange', onHashChange)
    // parse/defaultDestination are stable per-render for a given config.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const navigate = useCallback((destination, params = null) => {
    const next = hashFor(destination, params)
    if (next !== window.location.hash) {
      pendingRef.current += 1
      window.location.hash = next
    } else {
      // Same hash — force a state refresh so re-selecting a destination still
      // re-mounts / re-focuses the surface.
      setState((prev) => record(prev, parse()))
    }
  }, [parse, record])

  // Merge/replace surface-local params on the current destination.
  const setParams = useCallback((params, { replace = false } = {}) => {
    setState((prev) => {
      const merged = replace ? { ...(params || {}) } : { ...prev.params, ...(params || {}) }
      // drop nulls
      Object.keys(merged).forEach((k) => {
        if (merged[k] == null || merged[k] === '') delete merged[k]
      })
      const next = hashFor(prev.destination, merged)
      // history.replaceState avoids stacking a history entry per keystroke;
      // we still write the hash so deep-links + reloads work.
      try {
        window.history.replaceState(null, '', next)
      } catch {
        window.location.hash = next
      }
      return record(prev, { destination: prev.destination, params: merged })
    })
  }, [record])

  // Back (-1) / Forward (+1) through the console's history. The URL follows
  // with replaceState, so the step adds no browser entry of its own.
  const step = useCallback((delta) => {
    setState((prev) => {
      if (!canStep(prev.history, delta)) return prev
      const index = prev.history.index + delta
      const target = prev.history.entries[index]
      try {
        window.history.replaceState(null, '', hashFor(target.destination, target.params))
      } catch {
        // The view still moves; only the address bar lags.
      }
      return {
        destination: target.destination,
        params: target.params,
        history: { entries: prev.history.entries, index },
      }
    })
  }, [])
  const back = useCallback(() => step(-1), [step])
  const forward = useCallback(() => step(1), [step])

  return {
    destination: state.destination,
    params: state.params,
    navigate,
    setParams,
    back,
    forward,
    canBack: canStep(state.history, -1),
    canForward: canStep(state.history, 1),
  }
}
