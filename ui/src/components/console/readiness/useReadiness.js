import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  getHealth, getConnectors, getReadiness, listIntegrations, getAssertionRuns, getAssertions,
} from '../../../api/client.js'
import { buildHealthModel } from './healthModel.js'
import { buildConnectorLadder, tenantVerifiedNames, describeIntegration } from './connectorState.js'

/**
 * useReadiness — the ONE owner of readiness state.
 *
 * Fetches the diagnostic surfaces the console needs to answer "am I ready?",
 * and — this is the load-bearing part — keeps the DISTINCTION between
 * "answered false", "answered nothing" and "could not be asked".
 *
 * Every fetcher here resolves to a TRI-STATE, never a swallowed empty list:
 *   value  — the server answered
 *   null   — the endpoint is absent on this build (404), or the call failed
 * and each `null` becomes an `unknown` downstream, never a zero. Elsewhere in
 * this console a failed fetch degrades to `[]`, which renders as "nothing
 * configured" — indistinguishable from a healthy empty deployment. On a
 * readiness surface that ambiguity is the whole bug.
 */
export default function useReadiness({ scenarios = null, runs = null, pollMs = 30_000 } = {}) {
  const [healthBody, setHealthBody] = useState(undefined)   // undefined = not yet fetched
  const [healthError, setHealthError] = useState(null)
  const [integrations, setIntegrations] = useState(null)
  const [connectors, setConnectors] = useState(null)
  const [readinessBody, setReadinessBody] = useState(null)
  const [assertionRuns, setAssertionRuns] = useState(null)
  const [assertionCount, setAssertionCount] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lastRefreshedAt, setLastRefreshedAt] = useState(null)
  const mounted = useRef(true)

  // Set TRUE on every mount, not merely cleared on unmount: React 18 StrictMode
  // double-invokes effects, and a flag that is only ever cleared stays false
  // forever — the surface then renders a permanent UNKNOWN while the network
  // tab shows healthy 200s. (Same trap already paid for in useShelf.)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const refresh = useCallback(async () => {
    const settle = (setter) => (p) => p
      .then((v) => { if (mounted.current) setter(v) })
      .catch(() => { if (mounted.current) setter(null) })

    await Promise.all([
      getHealth()
        .then((b) => { if (mounted.current) { setHealthBody(b); setHealthError(null) } })
        .catch((e) => {
          if (!mounted.current) return
          setHealthBody(null)
          setHealthError(e?.message || 'SimCore did not answer /api/health')
        }),
      settle(setIntegrations)(listIntegrations()),
      settle(setReadinessBody)(getReadiness()),
      settle(setConnectors)(getConnectors().then((b) => (Array.isArray(b?.connectors) ? b.connectors : null))),
      settle(setAssertionRuns)(getAssertionRuns({ limit: 200 }).then((b) => (Array.isArray(b?.runs) ? b.runs : null))),
      settle(setAssertionCount)(getAssertions().then((b) => (typeof b?.total === 'number' ? b.total : null))),
    ])
    if (mounted.current) setLastRefreshedAt(new Date().toISOString())
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    refresh().finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [refresh])

  useEffect(() => {
    if (!pollMs) return undefined
    const t = setInterval(() => { refresh() }, pollMs)
    return () => clearInterval(t)
  }, [refresh, pollMs])

  const health = useMemo(
    () => (healthBody === undefined ? null : buildHealthModel(healthBody, { error: healthError })),
    [healthBody, healthError],
  )

  const ladder = useMemo(() => buildConnectorLadder({
    integrations, connectors, runs, assertionRuns, scenarios, assertionCount,
  }), [integrations, connectors, runs, assertionRuns, scenarios, assertionCount])

  const verifiedNames = useMemo(
    () => tenantVerifiedNames({ runs: runs || [], assertionRuns: assertionRuns || [] }),
    [runs, assertionRuns],
  )

  // name -> stored response_layer block (or null = nothing stored). When
  // /api/readiness could not be read the map is null and a tenant's
  // `responseLayer` stays undefined: unreadable is not the same as not probed.
  const responseLayers = useMemo(() => {
    const rows = readinessBody && Array.isArray(readinessBody.integrations)
      ? readinessBody.integrations : null
    if (!rows) return null
    const m = new Map()
    for (const r of rows) {
      if (r && r.name) m.set(r.name, r.response_layer === undefined ? null : r.response_layer)
    }
    return m
  }, [readinessBody])

  const tenants = useMemo(() => (
    (integrations || [])
      .filter((i) => i && (i.kind === 'xsiam' || i.kind === 'xsiam_tenant'))
      .map((i) => {
        const t = describeIntegration(i, { tenantVerifiedNames: verifiedNames })
        if (!t) return t
        const name = t.name
        return {
          ...t,
          responseLayer: responseLayers === null
            ? undefined
            : (responseLayers.has(name) ? responseLayers.get(name) : undefined),
        }
      })
      .filter(Boolean)
  ), [integrations, verifiedNames, responseLayers])

  return {
    loading,
    health,
    healthError,
    integrations,
    connectors,
    assertionRuns,
    assertionCount,
    ladder,
    tenants,
    lastRefreshedAt,
    refresh,
  }
}
