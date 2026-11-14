import { useCallback, useEffect, useRef, useState } from 'react'
import { getTenantEvidence, readTenantEvidence } from '../../api/client.js'

/**
 * useTenantEvidence — a run's tenant evidence, read on request only (spec §7.5).
 *
 * Sends ONE `GET /api/runs/{id}/tenant-evidence` (the stored snapshot; SimCore
 * makes no outbound call for it) when `runId` changes, and another after a
 * read. It sets NO interval and never reads the tenant on its own (A7): the
 * only POST is `read()`, which the operator triggers.
 *
 * `read()` is single-flight: a second call while one is in flight (a double
 * click, two handlers in one tick) returns the same promise and sends nothing.
 *
 * Returns `{ data, lastRead, loading, reading, error, read(opts), reload() }`:
 *   data      the GET answer `{code, status, snapshot, history, case_ledger}`
 *   lastRead  the latest POST answer for this run. It matters when the read
 *             stored nothing (`NO_TENANT_CONFIGURED`, `NO_RUN_WINDOW`) or was
 *             answered from the stored read (`TENANT_EVIDENCE_READ_RECENTLY`):
 *             the GET that follows cannot say either.
 *   error     the message of the last failed request, or null.
 *
 * @param {string|null} runId
 */
export default function useTenantEvidence(runId) {
  const [data, setData] = useState(null)
  const [lastRead, setLastRead] = useState(null)
  const [loading, setLoading] = useState(false)
  const [reading, setReading] = useState(false)
  const [error, setError] = useState(null)

  // The run whose answers may land. A switch from run A to run B while A's
  // request is in flight must not put A's evidence under B.
  const runRef = useRef(runId)
  runRef.current = runId
  // Monotonic GET id: only the newest GET may write `data`.
  const seqRef = useRef(0)
  // The in-flight read, so a second read() joins it instead of posting again.
  const inFlightRef = useRef(null)

  const reload = useCallback(async () => {
    const seq = ++seqRef.current
    const id = runId
    if (!id) { setData(null); return null }
    setLoading(true)
    try {
      const body = await getTenantEvidence(id)
      if (seq !== seqRef.current || runRef.current !== id) return null
      setData(body)
      setError(null)
      return body
    } catch (err) {
      if (seq !== seqRef.current || runRef.current !== id) return null
      setError(err?.message || String(err))
      return null
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [runId])

  const read = useCallback((opts = {}) => {
    if (inFlightRef.current) return inFlightRef.current
    const id = runId
    if (!id) return Promise.resolve(null)
    setReading(true)
    const run = (async () => {
      try {
        const body = await readTenantEvidence(id, opts)
        if (runRef.current !== id) return null
        setLastRead(body)
        setError(null)
        await reload()
        return body
      } catch (err) {
        if (runRef.current === id) setError(err?.message || String(err))
        return null
      } finally {
        // Only this read's own slot: a run switch may have started another.
        if (inFlightRef.current === run) inFlightRef.current = null
        if (runRef.current === id) setReading(false)
      }
    })()
    inFlightRef.current = run
    return run
  }, [runId, reload])

  // A different run starts from nothing — never from the previous run's evidence.
  useEffect(() => {
    setData(null)
    setLastRead(null)
    setError(null)
    setReading(false)
    inFlightRef.current = null
    reload()
  }, [reload])

  return { data, lastRead, loading, reading, error, read, reload }
}
