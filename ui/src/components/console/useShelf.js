import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getShelf, getShelfArtifacts, stagePayload } from '../../api/payloads.js'
import { normalizeShelf, shelfCounts } from './supplyState.js'

/**
 * useShelf — the ONE owner of payload-shelf state in the console.
 *
 * State + derived + actions, no JSX (the `useLaunchScenario.js` convention).
 *
 * NO POLLING, DELIBERATELY. The brief specified a 1 s job poll, but the shipped
 * `POST /api/shelf/stage` is SYNCHRONOUS — 201 with the staged artifact, or an
 * error. There is no job id to poll, so a timer here would burn a laptop
 * battery in a customer meeting for nothing. What we DO keep from that brief is
 * the hard client-side timeout: a request that never returns must resolve into
 * a named failure, because a spinner that spins forever is the same outcome as
 * a blank screen and the operator cannot tell whether the artifact landed.
 *
 * Cross-instance sync is a window event rather than a context. Two components
 * mount this hook (the catalog surface and the nav badge in AppConsole); after
 * a stage they must not disagree about what is on the shelf, and one CustomEvent
 * is cheaper than lifting a subsystem into the global provider. Precedent:
 * `cortex:navigate-ttp`.
 */

export const SHELF_CHANGED_EVENT = 'cortexsim:shelf-changed'

/** A stage request that has not returned in this long is reported as failed. */
export const STAGE_TIMEOUT_MS = 180_000

export default function useShelf({ adapters = [] } = {}) {
  const [payloadsBody, setPayloadsBody] = useState(null)
  const [artifactsBody, setArtifactsBody] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [available, setAvailable] = useState(null) // null = not yet known
  /** adapterId → { state:'staging'|'done'|'failed', error?, result? } */
  const [jobs, setJobs] = useState({})
  const mounted = useRef(true)

  // Set to TRUE on every mount, not just cleared on unmount. React 18's
  // StrictMode double-invokes effects (mount → unmount → mount) and any
  // destination switch remounts this hook; a flag that is only ever cleared
  // stays false forever, every setState is skipped, and the surface renders a
  // permanent UNKNOWN while the network tab shows a healthy 200. Found by
  // driving the real console in Chromium — no unit test would have caught it,
  // because Testing Library mounts once.
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const refresh = useCallback(async () => {
    try {
      const body = await getShelf()
      if (!mounted.current) return
      setPayloadsBody(body)
      setAvailable(true)
      setError(null)
    } catch (err) {
      if (!mounted.current) return
      setPayloadsBody(null)
      setAvailable(false)
      // A 404 is "this SimCore has no shelf endpoint" — a capability gap, not a
      // fault. Anything else is a real failure and keeps its message so the
      // banner can print the code instead of a generic shrug.
      setError(err?.status === 404 ? null : (err || new Error('shelf unavailable')))
    }
    // /artifacts is optional enrichment (pin type/ref, used_by). Its absence
    // must never blank the surface — hence a separate try.
    try {
      const arts = await getShelfArtifacts()
      if (mounted.current) setArtifactsBody(arts)
    } catch {
      if (mounted.current) setArtifactsBody(null)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    refresh().finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [refresh])

  // Another instance staged something — re-read rather than guess.
  useEffect(() => {
    const onChanged = () => { refresh() }
    window.addEventListener(SHELF_CHANGED_EVENT, onChanged)
    return () => window.removeEventListener(SHELF_CHANGED_EVENT, onChanged)
  }, [refresh])

  const shelf = useMemo(
    () => normalizeShelf(payloadsBody, artifactsBody),
    [payloadsBody, artifactsBody],
  )

  const counts = useMemo(() => shelfCounts(adapters, shelf), [adapters, shelf])

  /**
   * Stage one adapter's declared artifact onto THIS SimCore.
   *
   * Always sends `{adapter_id}` — never a name/url pair assembled in the
   * browser. The pack is where the URL and the pin live, so the staged bytes
   * are the bytes the pack was authored against.
   */
  const stage = useCallback(async (adapterId, { replace = false } = {}) => {
    setJobs((j) => ({ ...j, [adapterId]: { state: 'staging' } }))
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), STAGE_TIMEOUT_MS)
    try {
      const result = await stagePayload({ adapter_id: adapterId, replace }, { signal: controller.signal })
      clearTimeout(timer)
      if (!mounted.current) return result
      setJobs((j) => ({ ...j, [adapterId]: { state: 'done', result } }))
      await refresh()
      // Tell the other instance(s). Fired AFTER our own refresh so the surface
      // the operator is looking at is never the last to update.
      try { window.dispatchEvent(new CustomEvent(SHELF_CHANGED_EVENT)) } catch { /* jsdom */ }
      return result
    } catch (err) {
      clearTimeout(timer)
      const timedOut = err?.name === 'AbortError'
      const failure = timedOut
        ? {
            message: `Staging timed out after ${Math.round(STAGE_TIMEOUT_MS / 1000)}s. SimCore may be `
              + 'behind a proxy holding the connection open. The artifact was NOT staged.',
            code: 'PAYLOAD_STAGE_TIMEOUT',
            status: null,
          }
        : { message: err?.message || 'Staging failed', code: err?.code || null, status: err?.status ?? null }
      if (mounted.current) setJobs((j) => ({ ...j, [adapterId]: { state: 'failed', error: failure } }))
      return null
    }
  }, [refresh])

  const clearJob = useCallback((adapterId) => {
    setJobs((j) => {
      if (!(adapterId in j)) return j
      const next = { ...j }
      delete next[adapterId]
      return next
    })
  }, [])

  return {
    // state
    shelf,
    loading,
    error,
    available,
    jobs,
    // derived
    counts,
    writable: shelf.writable,
    // actions
    stage,
    clearJob,
    refresh,
  }
}
