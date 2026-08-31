import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { tourSeen, markTourSeen } from './onboardingState.js'

export function anchorExists(anchorId) {
  try {
    return Boolean(document.querySelector(`[data-tour-id="${anchorId}"]`))
  } catch { return false }
}

export const DEFAULT_ANCHOR_TIMEOUT_MS = 1500

/**
 * waitForAnchor — resolves `true` the moment `[data-tour-id="anchorId"]`
 * lands in the DOM, `false` once `timeoutMs` elapses without it.
 *
 * Every destination surface is `lazy()` + `Suspense` (`ui/src/app/
 * destinations.jsx`) and exactly one is mounted at a time. A synchronous
 * `document.querySelector` right after `onNavigate` fires cannot tell "this
 * anchor will never exist" apart from "this anchor's chunk just hasn't
 * finished loading yet" — on a cold chunk cache that ambiguity silently
 * deleted stops 2 and 4 (C2), the exact stop the tour exists to reach.
 * MutationObserver reacts to the DOM as soon as it actually changes rather
 * than guessing a fixed delay, so a fast mount resolves fast and a stop
 * whose anchor truly never appears still gives up, just not instantly.
 */
export function waitForAnchor(anchorId, timeoutMs = DEFAULT_ANCHOR_TIMEOUT_MS) {
  return new Promise((resolve) => {
    if (anchorExists(anchorId)) { resolve(true); return }
    let settled = false
    let observer = null
    // eslint-disable-next-line prefer-const
    let timer
    const settle = (result) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (observer) observer.disconnect()
      resolve(result)
    }
    timer = setTimeout(() => settle(false), timeoutMs)
    try {
      observer = new MutationObserver(() => {
        if (anchorExists(anchorId)) settle(true)
      })
      observer.observe(document.body, { childList: true, subtree: true })
    } catch {
      // MutationObserver unavailable in this environment — the hard timeout
      // above still fires, so this degrades to "never skip early" rather
      // than "never skip at all".
    }
  })
}

function debugSkip(stopId, anchorId) {
  try {
    // eslint-disable-next-line no-console
    console.debug(`[tour] skip stop "${stopId}" — anchor "${anchorId}" did not mount within the wait window`)
  } catch { /* no console in this environment */ }
}

/**
 * useTour — idle → running(index) → done.
 *
 * The invariants that matter are the failure ones: a stop whose anchor is not
 * mounted is SKIPPED (after a bounded wait — see `waitForAnchor` above, not
 * a synchronous probe), a tour with no mountable stop exits immediately, and
 * every exit path marks the tour seen. A spotlight that points at nothing with
 * no way out is the characteristic failure of this kind of feature.
 *
 * `next()`, `prev()` and `start()` all navigate to a candidate stop's
 * destination BEFORE checking its anchor, and both directions share the same
 * wait contract. `prev()` used to probe the CURRENT destination's DOM
 * without navigating first, which deterministically walked backward past any
 * stop anchored in a different, currently-unmounted destination (I3) —
 * "Back" from the last stop skipped straight past both lazy-anchored stops
 * every time, not just on a slow network.
 */
export function useTour({ stops, onNavigate, autoStart = false, anchorTimeoutMs = DEFAULT_ANCHOR_TIMEOUT_MS }) {
  const [index, setIndex] = useState(-1)

  // Bumped by every call that supersedes an in-flight search (a new
  // next/prev/start, or an exit) so a slow `waitForAnchor` resolving late
  // cannot land a stale `setIndex` after the tour has already moved on or
  // closed.
  const requestIdRef = useRef(0)
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])

  const finish = useCallback(() => {
    requestIdRef.current += 1
    setIndex(-1)
    markTourSeen()
  }, [])

  // Walks stops in `dir` (+1 or -1) from `from`, navigating to and awaiting
  // each candidate's anchor before deciding whether to land there or skip.
  const search = useCallback(async (from, dir, myRequestId) => {
    for (let i = from; i >= 0 && i < stops.length; i += dir) {
      const candidate = stops[i]
      if (onNavigate) onNavigate(candidate.destination)
      // eslint-disable-next-line no-await-in-loop
      const ok = await waitForAnchor(candidate.anchor, anchorTimeoutMs)
      if (requestIdRef.current !== myRequestId || !mountedRef.current) return -1
      if (ok) return i
      debugSkip(candidate.id, candidate.anchor)
    }
    return -1
  }, [stops, onNavigate, anchorTimeoutMs])

  const goTo = useCallback(async (from, dir) => {
    const myRequestId = ++requestIdRef.current
    const i = await search(from, dir, myRequestId)
    if (requestIdRef.current !== myRequestId || !mountedRef.current) return
    if (i === -1) { finish(); return }
    setIndex(i)
  }, [search, finish])

  const start = useCallback(() => goTo(0, 1), [goTo])

  useEffect(() => {
    if (!autoStart) return
    if (tourSeen()) return
    goTo(0, 1)
    // Intentionally NOT depending on `goTo` — the identity churns every
    // render (it closes over `stops`/`onNavigate`), and this effect must
    // fire once per mount, not on every re-render, or a fresh `goTo` would
    // race the in-flight one from the previous render's closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart])

  const next = useCallback(() => goTo(index + 1, 1), [goTo, index])

  // prev() shares `search`'s navigate-then-await contract with next() —
  // walking backward must be able to re-mount a destination the same way
  // forward progress does (I3).
  const prev = useCallback(() => {
    if (index <= 0) return Promise.resolve()
    return goTo(index - 1, -1)
  }, [goTo, index])

  const stop = index >= 0 ? stops[index] : null
  const total = useMemo(() => stops.length, [stops])

  return { active: index >= 0, stop, index, total, next, prev, exit: finish, start }
}
