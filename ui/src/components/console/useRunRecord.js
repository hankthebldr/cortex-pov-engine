/**
 * useRunRecord — one run's record (`GET /api/runs/{id}`), kept current while
 * the run is live.
 *
 * The Runs list is polled with `fields=summary`, so its rows carry no output
 * and no `step_outcomes`; the run's page reads the record itself. While the
 * run is live the record is re-read on every frame of the run's event stream
 * (status, output) and every few seconds besides, so the page follows the run
 * instead of waiting on the list's poll. A settled run is read once, and again
 * when `refreshKey` changes (the row's status or output size moved).
 *
 * The record, not the frame, is what the page shows: a frame is only the
 * reason to read again. The one exception is output streamed while a step
 * runs, which the record keeps only as a tail: the frames are handed back as
 * `events` so the page can append them between reads (liveOutput.js).
 *
 * @param {string|null} runId
 * @param {Object}  [opts]
 * @param {boolean} [opts.settled]     the caller already knows the run ended
 * @param {string}  [opts.refreshKey]  read again when this changes
 * @returns {{ record: Object|null, failed: boolean,
 *             stream: {connected: boolean, mode: string}|null,
 *             events: Array }}
 *   `stream` is null once nothing is being followed; `events` are the run's
 *   stream frames so far, oldest first.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { getRun } from '../../api/client.js'
import { idMatches } from '../../api/ids.js'
import { isRunSettled } from './runStatus.js'
import useRunEventStream from './useRunEventStream.js'

const POLL_MS = 3_000
const FRAME_DEBOUNCE_MS = 250

export default function useRunRecord(runId, { settled = false, refreshKey = '' } = {}) {
  const [record, setRecord] = useState(null)
  const [failed, setFailed] = useState(false)
  const inflight = useRef(null)
  const wanted = useRef(runId)
  wanted.current = runId

  useEffect(() => { setRecord(null); setFailed(false) }, [runId])

  const live = !!runId && !settled && !isRunSettled(record?.status)
  const { events, connected, mode } = useRunEventStream(live ? runId : null)

  const read = useCallback(async () => {
    if (!runId || inflight.current === runId) return
    inflight.current = runId
    try {
      const d = await getRun(runId)
      if (wanted.current !== runId) return
      // Only a record that is recognisably this run's replaces what is shown:
      // a transient bad read must not blank a log the DC is reading.
      const isThisRun = d && typeof d === 'object'
        && (d.run_id != null ? idMatches(d.run_id, runId) : typeof d.output === 'string')
      if (isThisRun) { setRecord(d); setFailed(false) } else setFailed(true)
    } catch {
      if (wanted.current === runId) setFailed(true)
    } finally {
      if (inflight.current === runId) inflight.current = null
    }
  }, [runId])

  useEffect(() => { read() }, [read, refreshKey])

  useEffect(() => {
    if (!live) return undefined
    const t = setInterval(read, POLL_MS)
    return () => clearInterval(t)
  }, [live, read])

  const frames = events.length
  useEffect(() => {
    if (!live || frames === 0) return undefined
    const t = setTimeout(read, FRAME_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [live, frames, read])

  return { record, failed, stream: live ? { connected, mode } : null, events }
}
