import { useCallback, useEffect, useState } from 'react'

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
 * @param {Object}  opts
 * @param {string}  opts.defaultDestination  fallback when the hash is empty/unknown
 * @param {(id:string)=>boolean} [opts.isValid]  guard for unknown destinations
 */
export default function useConsoleRouter({ defaultDestination = 'library', isValid } = {}) {
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

  const [state, setState] = useState(parse)

  useEffect(() => {
    const onHashChange = () => setState(parse())
    window.addEventListener('hashchange', onHashChange)
    // Normalize an empty hash to the default destination on mount so the URL
    // is always meaningful (and back/forward has something to return to).
    if (!window.location.hash) {
      window.location.replace(`#/${defaultDestination}`)
    } else {
      setState(parse())
    }
    return () => window.removeEventListener('hashchange', onHashChange)
    // parse/defaultDestination are stable per-render for a given config.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const buildHash = useCallback((destination, params) => {
    const keys = params ? Object.keys(params).filter((k) => params[k] != null && params[k] !== '') : []
    if (!keys.length) return `#/${destination}`
    const sp = new URLSearchParams()
    keys.forEach((k) => sp.set(k, String(params[k])))
    return `#/${destination}?${sp.toString()}`
  }, [])

  const navigate = useCallback((destination, params = null) => {
    const next = buildHash(destination, params)
    if (next !== window.location.hash) {
      window.location.hash = next
    } else {
      // Same hash — force a state refresh so re-selecting a destination still
      // re-mounts / re-focuses the surface.
      setState(parse())
    }
  }, [buildHash, parse])

  // Merge/replace surface-local params on the current destination.
  const setParams = useCallback((params, { replace = false } = {}) => {
    setState((prev) => {
      const merged = replace ? { ...(params || {}) } : { ...prev.params, ...(params || {}) }
      // drop nulls
      Object.keys(merged).forEach((k) => {
        if (merged[k] == null || merged[k] === '') delete merged[k]
      })
      const next = buildHash(prev.destination, merged)
      // history.replaceState avoids stacking a history entry per keystroke;
      // we still write the hash so deep-links + reloads work.
      try {
        window.history.replaceState(null, '', next)
      } catch {
        window.location.hash = next
      }
      return { destination: prev.destination, params: merged }
    })
  }, [buildHash])

  return {
    destination: state.destination,
    params: state.params,
    navigate,
    setParams,
  }
}
