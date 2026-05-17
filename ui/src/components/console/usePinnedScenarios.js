import { useState, useEffect, useCallback } from 'react'

const STORAGE_KEY = 'cortexsim.pinnedScenarios.v1'
const MAX_PINNED  = 12

/**
 * usePinnedScenarios — localStorage-backed list of pinned scenario IDs.
 *
 * The list is ordered: most-recently-pinned first. Cross-tab sync via the
 * browser `storage` event so opening the console in two tabs stays consistent.
 *
 * Returns { pinnedIds, isPinned, pin, unpin, toggle, clear }.
 */
export default function usePinnedScenarios() {
  const [pinnedIds, setPinnedIds] = useState(() => readFromStorage())

  // Cross-tab sync
  useEffect(() => {
    const handler = (e) => {
      if (e.key !== STORAGE_KEY) return
      setPinnedIds(readFromStorage())
    }
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [])

  const persist = useCallback((next) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      /* storage quota or unavailable; in-memory only */
    }
    setPinnedIds(next)
  }, [])

  const isPinned = useCallback(
    (id) => !!id && pinnedIds.includes(id),
    [pinnedIds]
  )

  const pin = useCallback((id) => {
    if (!id) return
    setPinnedIds((prev) => {
      if (prev.includes(id)) return prev
      const next = [id, ...prev].slice(0, MAX_PINNED)
      try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch {}
      return next
    })
  }, [])

  const unpin = useCallback((id) => {
    if (!id) return
    setPinnedIds((prev) => {
      const next = prev.filter((p) => p !== id)
      try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch {}
      return next
    })
  }, [])

  const toggle = useCallback((id) => {
    if (!id) return
    setPinnedIds((prev) => {
      const next = prev.includes(id)
        ? prev.filter((p) => p !== id)
        : [id, ...prev].slice(0, MAX_PINNED)
      try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next)) } catch {}
      return next
    })
  }, [])

  const clear = useCallback(() => persist([]), [persist])

  return { pinnedIds, isPinned, pin, unpin, toggle, clear }
}

function readFromStorage() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter((s) => typeof s === 'string') : []
  } catch {
    return []
  }
}
