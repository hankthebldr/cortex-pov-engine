import { useCallback, useEffect, useMemo, useState } from 'react'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { readConfirmed, setupProgress, writeConfirmed } from './setupProgress.js'

const EVT = 'cortexsim:setup-confirmed'

/**
 * The DC's "you confirmed" ticks, kept current across every reader in the
 * page (the rail's component dots read them as well as Get started).
 */
export function useConfirmed() {
  const [confirmed, setConfirmed] = useState(readConfirmed)
  useEffect(() => {
    const sync = () => setConfirmed(readConfirmed())
    window.addEventListener(EVT, sync)
    return () => window.removeEventListener(EVT, sync)
  }, [])
  return confirmed
}

/**
 * Record (or withdraw) one "you confirmed" tick, and tell every reader.
 * Keys are the Get started step ids ('collector', 'bvm') and, for the
 * Configure frame, '<component>:<step id>'.
 */
export function setConfirmedKey(key, value) {
  writeConfirmed({ ...readConfirmed(), [key]: !!value })
  window.dispatchEvent(new Event(EVT))
}

/**
 * The Get started checklist as a hook. The rail badge and the page both call
 * it, so a confirmation made on the page updates the badge in the same render
 * pass (via a window event) instead of on the next reload.
 */
export default function useSetupProgress() {
  const { agents, tenants, runs } = useEnvironment()
  const confirmed = useConfirmed()

  // Every reader — this hook included — re-reads on the event.
  const setStepConfirmed = useCallback((id, value) => setConfirmedKey(id, value), [])

  const progress = useMemo(
    () => setupProgress({ agents, tenants, runs, confirmed }),
    [agents, tenants, runs, confirmed],
  )
  return { ...progress, setStepConfirmed }
}
