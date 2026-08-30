import { useCallback, useEffect, useMemo, useState } from 'react'
import { tourSeen, markTourSeen } from './onboardingState.js'

export function anchorExists(anchorId) {
  try {
    return Boolean(document.querySelector(`[data-tour-id="${anchorId}"]`))
  } catch { return false }
}

/**
 * useTour — idle → running(index) → done.
 *
 * The invariants that matter are the failure ones: a stop whose anchor is not
 * mounted is SKIPPED, a tour with no mountable stop exits immediately, and
 * every exit path marks the tour seen. A spotlight that points at nothing with
 * no way out is the characteristic failure of this kind of feature.
 */
export function useTour({ stops, onNavigate, autoStart = false }) {
  const [index, setIndex] = useState(-1)

  const firstMountable = useCallback(
    (from) => {
      for (let i = from; i < stops.length; i += 1) {
        if (anchorExists(stops[i].anchor)) return i
      }
      return -1
    },
    [stops],
  )

  const finish = useCallback(() => {
    setIndex(-1)
    markTourSeen()
  }, [])

  const start = useCallback(() => {
    const i = firstMountable(0)
    if (i === -1) { markTourSeen(); return }
    setIndex(i)
  }, [firstMountable])

  useEffect(() => {
    if (!autoStart) return
    if (tourSeen()) return
    const i = firstMountable(0)
    if (i === -1) { markTourSeen(); return }
    setIndex(i)
  }, [autoStart, firstMountable])

  // Navigate whenever the active stop changes.
  useEffect(() => {
    if (index < 0 || !stops[index]) return
    if (onNavigate) onNavigate(stops[index].destination)
  }, [index, stops, onNavigate])

  const next = useCallback(() => {
    const i = firstMountable(index + 1)
    if (i === -1) { finish(); return }
    setIndex(i)
  }, [index, firstMountable, finish])

  const prev = useCallback(() => {
    for (let i = index - 1; i >= 0; i -= 1) {
      if (anchorExists(stops[i].anchor)) { setIndex(i); return }
    }
  }, [index, stops])

  const stop = index >= 0 ? stops[index] : null
  const total = useMemo(() => stops.length, [stops])

  return { active: index >= 0, stop, index, total, next, prev, exit: finish, start }
}
