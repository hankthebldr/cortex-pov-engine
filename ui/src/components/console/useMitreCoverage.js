import { useState, useEffect, useCallback } from 'react'
import { getMitreCoverage } from '../../api/client.js'

/**
 * useMitreCoverage — fetches /api/mitre/coverage with explicit refresh.
 *
 * Returns the same payload shape as the legacy heatmap consumed:
 *   {
 *     summary: { total_techniques, detected, run_not_detected, not_run },
 *     by_tactic: [{ tactic_id, tactic_name, techniques: [...] }]
 *   }
 *
 * Each technique:
 *   { technique_id, technique_name, status, scenarios, observed_detections,
 *     total_detections, coverage_pct, planes, tactic_id, tactic_name }
 *
 * @param {string|null} scopeKey  Opaque ambient-scope key (e.g. the active
 *   XSIAM tenant name). When it changes, the hook refetches so the coverage
 *   matrix reflects the currently-selected environment without a manual
 *   Refresh. Pass `null` (the default) for global, scope-independent loads.
 */
export default function useMitreCoverage(scopeKey = null) {
  const [data, setData]         = useState(null)
  const [loading, setLoading]   = useState(true)
  const [error, setError]       = useState(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await getMitreCoverage()
      setData(d)
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  // Initial load + re-load whenever the ambient scope key changes. `refresh`
  // is stable, so the effect fires on mount and on every scopeKey transition.
  useEffect(() => { refresh() }, [refresh, scopeKey])

  return { data, loading, error, refresh }
}
