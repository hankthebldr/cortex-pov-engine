/**
 * useReadBack — the console's one "Read back" (design v26 §4.6).
 *
 * Read back asks SimCore to read the tenant for a finished run
 * (POST /api/runs/{id}/reconcile?connector=xsiam): pull the alerts in the
 * run's window, match them to what it expected, score it. It is read-only
 * toward Cortex. While it is in flight the run is `reading`; when it lands the
 * run list is re-read, so the phase comes from what SimCore recorded rather
 * than from this browser's memory.
 *
 * A read back that could not read anything is not a result. With no tenant
 * credential SimCore answers NO_INTEGRATION, and the run stays unread with
 * that reason beside it — never "ready", never a zero.
 *
 * The state (which runs are reading, the last outcome per run) is shared by
 * ReadBackProvider so the Runs list, the run's page and Results agree. Without
 * the provider each caller keeps its own copy, which is enough for one surface.
 */
import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react'
import { reconcileRun } from '../../api/client.js'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import useToast from './useToast.js'

void React

const ReadBackContext = createContext(null)

/** Why nothing was read, in the DC's words. */
export function readBackFailure(err) {
  const code = err && err.code
  if (code === 'NO_INTEGRATION') {
    return 'No tenant credential is configured, so nothing was read. Configure the tenant, then Read back. This says nothing about the customer’s coverage.'
  }
  if (code === 'CREDENTIAL_ERROR') {
    return 'The stored tenant credential could not be decrypted (was the master key changed?). Enter it again on Tenant, then Read back.'
  }
  if (code === 'INTEGRATION_NOT_FOUND') {
    return 'The tenant integration this read named is not registered. Check Tenant, then Read back.'
  }
  if (code === 'CONNECTOR_PULL_FAILED') {
    return `The tenant read failed: ${err.message || 'no reason given'}. Nothing was read; Read back again once the tenant answers.`
  }
  return `Nothing was read back: ${(err && err.message) || 'SimCore gave no reason'}.`
}

/** What a read back that did read the tenant found, in one line. */
export function readBackSummary(summary) {
  if (!summary || typeof summary !== 'object') return 'Read back.'
  if (summary.never_delivered) {
    return 'Nothing to read: no step of this run reached its agent.'
  }
  const from = typeof summary.source === 'string' ? summary.source : 'the tenant'
  const parts = []
  if (Number.isFinite(summary.observed) && Number.isFinite(summary.denominator)) {
    parts.push(`${summary.observed} of ${summary.denominator} detected`)
  }
  if (Number.isFinite(summary.queries_issued)) {
    parts.push(`${summary.queries_issued} quer${summary.queries_issued === 1 ? 'y' : 'ies'}`)
  }
  return `Read back from ${from}${parts.length ? ` — ${parts.join(' · ')}` : ''}.`
}

function useReadBackState() {
  const { refreshRuns } = useEnvironment()
  const toast = useToast()
  const [reading, setReading] = useState(() => new Set())
  const [outcomes, setOutcomes] = useState(() => new Map())
  // A second click while the first read is in flight must not send another.
  const inFlight = useRef(new Set())

  const readBack = useCallback(async (runId) => {
    if (!runId || inFlight.current.has(runId)) return null
    inFlight.current.add(runId)
    setReading((prev) => new Set(prev).add(runId))
    let outcome
    try {
      const summary = await reconcileRun(runId)
      outcome = { ok: true, message: readBackSummary(summary), summary }
      toast(outcome.message, 'success')
    } catch (err) {
      outcome = { ok: false, code: err?.code || null, message: readBackFailure(err) }
      toast(outcome.message, 'warn', 4000)
    } finally {
      inFlight.current.delete(runId)
    }
    setOutcomes((prev) => new Map(prev).set(runId, outcome))
    // Re-read the runs first, then leave `reading`: the row must not flash
    // back to "Read back" before the list says what SimCore recorded.
    try { await refreshRuns() } catch { /* the poll will catch up */ }
    setReading((prev) => {
      const next = new Set(prev)
      next.delete(runId)
      return next
    })
    return outcome
  }, [refreshRuns, toast])

  return useMemo(() => ({ reading, outcomes, readBack }), [reading, outcomes, readBack])
}

export function ReadBackProvider({ children }) {
  const value = useReadBackState()
  return <ReadBackContext.Provider value={value}>{children}</ReadBackContext.Provider>
}

/** @returns {{ reading: Set<string>, outcomes: Map<string, {ok, message, code?}>, readBack: (runId) => Promise }} */
export default function useReadBack() {
  const shared = useContext(ReadBackContext)
  const local = useReadBackState()
  return shared || local
}
