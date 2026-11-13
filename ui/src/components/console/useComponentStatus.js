import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react'
import { useEnvironment } from '../../context/EnvironmentContext.jsx'
import { listCollectorTargets, listFirewalls } from '../../api/client.js'
import { componentStatuses, issueCount } from './componentModel.js'
import { useConfirmed } from './useSetupProgress.js'

/**
 * useComponentStatus — every component's status, from real state.
 *
 * Most of what the model needs the EnvironmentProvider already polls (health,
 * agents, tenants). The two lists it does not hold — collector targets and the
 * firewall registry — are read here, into ONE module-level store, so the rail
 * and whichever page is open share a single read instead of each fetching its
 * own copy and disagreeing for a poll interval.
 *
 * The store is re-read on mount of each subscriber and on `refresh()`, which a
 * page calls after it changed something (registered a firewall, ran a Test).
 * It is current state only — nothing here keeps a history (v26 §4.2).
 */

const EMPTY = Object.freeze({
  collectorTargets: null,
  collectorError: null,
  firewalls: null,
  firewallError: null,
})

let state = EMPTY
let inflight = null
const subscribers = new Set()

function publish(next) {
  state = next
  subscribers.forEach((fn) => fn())
}

function errText(err, fallback) {
  return err?.message || fallback
}

/** Re-read collector targets and firewalls. Concurrent callers share one read. */
export function refreshComponentSources() {
  if (inflight) return inflight
  inflight = Promise.allSettled([listCollectorTargets(), listFirewalls()])
    .then(([ct, fw]) => {
      const next = { ...state }
      if (ct.status === 'fulfilled' && Array.isArray(ct.value?.targets)) {
        next.collectorTargets = ct.value.targets
        next.collectorError = null
      } else {
        // An unrecognised envelope is a failure to read, not "no targets".
        next.collectorError = ct.status === 'rejected'
          ? errText(ct.reason, 'collector targets failed')
          : 'GET /api/collector-targets returned no targets[] list'
      }
      if (fw.status === 'fulfilled' && Array.isArray(fw.value?.firewalls)) {
        next.firewalls = fw.value.firewalls
        next.firewallError = null
      } else {
        next.firewallError = fw.status === 'rejected'
          ? errText(fw.reason, 'firewall list failed')
          : 'GET /api/ngfw returned no firewalls[] list'
      }
      publish(next)
    })
    .finally(() => { inflight = null })
  return inflight
}

/** Test seam: forget what was read, so each test starts from nothing. */
export function __resetComponentSources() {
  state = EMPTY
  inflight = null
}

function subscribe(fn) {
  subscribers.add(fn)
  return () => subscribers.delete(fn)
}
const getSnapshot = () => state

export default function useComponentStatus() {
  const env = useEnvironment()
  const sources = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  const confirmed = useConfirmed()

  useEffect(() => { refreshComponentSources() }, [])

  const statuses = useMemo(() => componentStatuses({
    healthModel: env.healthModel,
    agents: env.agents,
    agentsLoaded: !env.loading?.agents,
    agentsError: env.agents?.length ? null : env.errors?.agents || null,
    tenants: env.tenants,
    tenantsLoaded: !env.loading?.tenants,
    tenantsError: env.tenants?.length ? null : env.errors?.tenants || null,
    ...sources,
    confirmed,
  }), [env.healthModel, env.agents, env.loading, env.errors, env.tenants, sources, confirmed])

  const refresh = useCallback(() => refreshComponentSources(), [])

  return { statuses, issues: issueCount(statuses), refresh }
}
