import { useEffect, useState } from 'react'
import { getScenarios, getUcTcTestCases, getUcTcUseCases } from '../../api/client.js'
import { BINDING_STATUS, buildCardBindings } from './catalogBindings.js'

const EMPTY = Object.freeze({
  reason: '',
  byCard: new Map(),
  scenariosByCard: new Map(),
  indexVersion: null,
})

/**
 * Fetch the three lists the Catalog joins to place a TTP card under a use
 * case, test case and FY27 subdomain, and run the join once.
 *
 * Deliberately NOT blocking: the card grid renders from `/api/ttps` alone and
 * the organize controls light up when this resolves. Every non-OK outcome is a
 * named status with a reason (see `catalogBindings.js`) — the caller renders
 * it as degraded, never as an empty result.
 *
 * @returns {{status: string, reason: string, byCard: Map, scenariosByCard: Map,
 *            indexVersion: ?string}}
 */
export default function useCatalogBindings() {
  const [state, setState] = useState({ ...EMPTY, status: BINDING_STATUS.LOADING })

  useEffect(() => {
    let cancelled = false
    Promise.all([getScenarios(), getUcTcTestCases(), getUcTcUseCases()])
      .then(([scenarios, tcs, ucs]) => {
        if (cancelled) return
        // `test_cases` / `use_cases` are passed through unguarded on purpose:
        // a payload without them is an unrecognised shape, and
        // buildCardBindings raises on it instead of reading it as empty.
        const built = buildCardBindings({
          scenarios,
          testCases: tcs?.test_cases,
          useCases: ucs?.use_cases,
          indexLoaded: tcs?.index_loaded === true && ucs?.index_loaded === true,
        })
        setState({ ...built, indexVersion: tcs?.index_version ?? null })
      })
      .catch((e) => {
        if (cancelled) return
        setState({
          ...EMPTY,
          status: BINDING_STATUS.ERROR,
          reason: `The scenario list or the UC / TC index could not be loaded (${e?.message || 'request failed'}).`,
        })
      })
    return () => { cancelled = true }
  }, [])

  return state
}
