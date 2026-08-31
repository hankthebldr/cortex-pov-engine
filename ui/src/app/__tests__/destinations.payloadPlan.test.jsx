/**
 * I-1 — a launch could silently omit the payload plan.
 *
 * `useDecodedPlan` used to return a bare `plan` that was `null` both when
 * no `?plan=` param existed AND while a composed plan's chunk
 * (ToolAdapterCatalog.jsx, resolved via dynamic import) was still in
 * flight. Those are not the same state: the consultant clicking Launch in
 * the second window would fire a run with no `payload_plan`, no error, no
 * warning — the exact manufactured false-negative the payload shelf exists
 * to prevent. This locks the fix: the hook must expose the two states
 * distinctly as `{ plan, resolving }`.
 */
import { describe, it, expect } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useDecodedPlan } from '../destinations.jsx'

// Mirrors ToolAdapterCatalog.jsx::encodePlan's wire format closely enough
// for decodePlan to round-trip it — the exact bytes don't matter here, only
// that decoding eventually succeeds and produces a non-null plan.
function encode(obj) {
  return btoa(unescape(encodeURIComponent(JSON.stringify(obj))))
}

describe('useDecodedPlan — I-1 resolving vs. no-plan', () => {
  it('is resolving:true with plan:null the instant a `?plan=` param is present, before the chunk settles', () => {
    const encoded = encode({ v: 1, scenario_id: 'SIM-CDR-001', artifacts: [{ adapter_id: 'TOOL-A' }] })
    const { result } = renderHook(() => useDecodedPlan(encoded))
    // Synchronous, first-paint assertion — this is the exact window I-1 was about.
    expect(result.current).toEqual({ plan: null, resolving: true })
  })

  it('settles to resolving:false with the decoded plan once the chunk resolves', async () => {
    const encoded = encode({
      v: 1,
      scenario_id: 'SIM-CDR-001',
      artifacts: [{ adapter_id: 'TOOL-LINPEAS', payload_name: 'linpeas.sh' }],
    })
    const { result } = renderHook(() => useDecodedPlan(encoded))
    await waitFor(() => expect(result.current.resolving).toBe(false))
    expect(result.current.plan).toMatchObject({ scenario_id: 'SIM-CDR-001' })
    expect(result.current.plan.artifacts).toHaveLength(1)
  })

  it('is resolving:false, plan:null (not the same shape rendered by a bug) when no plan was ever composed', () => {
    const { result } = renderHook(() => useDecodedPlan(null))
    expect(result.current).toEqual({ plan: null, resolving: false })
  })
})
