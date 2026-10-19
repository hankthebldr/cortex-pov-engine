import { useCallback, useEffect, useMemo, useState } from 'react'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { readConfirmed, setupProgress, writeConfirmed } from './setupProgress.js'

const EVT = 'cortexsim:setup-confirmed'

/**
 * The Get started checklist as a hook. The rail badge and the page both call
 * it, so a confirmation made on the page updates the badge in the same render
 * pass (via a window event) instead of on the next reload.
 */
export default function useSetupProgress() {
  const { agents, tenants, runs } = useEnvironment()
  const [confirmed, setConfirmed] = useState(readConfirmed)

  useEffect(() => {
    const sync = () => setConfirmed(readConfirmed())
    window.addEventListener(EVT, sync)
    return () => window.removeEventListener(EVT, sync)
  }, [])

  const setStepConfirmed = useCallback((id, value) => {
    const next = { ...readConfirmed(), [id]: !!value }
    writeConfirmed(next)
    setConfirmed(next)
    window.dispatchEvent(new Event(EVT))
  }, [])

  const progress = useMemo(
    () => setupProgress({ agents, tenants, runs, confirmed }),
    [agents, tenants, runs, confirmed],
  )
  return { ...progress, setStepConfirmed }
}
