/**
 * useEnvironment — the read hook for the console's ambient scope.
 *
 * Re-exports the hook defined alongside the provider so surfaces import from a
 * single, stable path. Also exposes narrow, memoized selector hooks so a
 * high-churn update (a run poll) can be subscribed to without every consumer
 * (e.g. the Library) re-deriving unrelated slices.
 */
import { useMemo } from 'react'
import { useEnvironment } from './EnvironmentContext.jsx'

export { useEnvironment }
export default useEnvironment

/** Low-churn scope: the active tenant/agent + composed health. */
export function useScope() {
  const { tenant, tenants, agent, agents, health, setTenant, setAgent } = useEnvironment()
  return useMemo(
    () => ({ tenant, tenants, agent, agents, health, setTenant, setAgent }),
    [tenant, tenants, agent, agents, health, setTenant, setAgent],
  )
}

/** The scenario corpus + derived planes + pin controls (Library's slice). */
export function useCorpus() {
  const { scenarios, planes, pinnedIds, isPinned, togglePin, unpin, loading } = useEnvironment()
  return useMemo(
    () => ({ scenarios, planes, pinnedIds, isPinned, togglePin, unpin, loading }),
    [scenarios, planes, pinnedIds, isPinned, togglePin, unpin, loading],
  )
}

/** The run list + derived active/last run (Runs & Proof's slice). */
export function useRunScope() {
  const { runs, activeRun, lastRun, refreshRuns } = useEnvironment()
  return useMemo(
    () => ({ runs, activeRun, lastRun, refreshRuns }),
    [runs, activeRun, lastRun, refreshRuns],
  )
}
