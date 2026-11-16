/**
 * The console's own Back / Forward history (design C9).
 *
 * Two layers:
 *   1. navHistory.js, pure: what a move does to the list.
 *   2. useConsoleRouter: `back()` / `forward()` / `canBack` / `canForward`
 *      through real hash navigation, `setParams` and the browser's own
 *      hashchange.
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

import {
  HISTORY_LIMIT, canStep, navKey, recordLocation, startHistory,
} from '../navHistory.js'
import useConsoleRouter from '../useConsoleRouter.js'
import { runsTabRedirect, resultTab, RESULT_TABS } from '../resultTabs.js'

const loc = (destination, params = {}) => ({ destination, params })
const keys = (h) => h.entries.map(navKey)

describe('navHistory — what a move does to the list', () => {
  it('a new place is a new entry; a filter change rewrites the current one', () => {
    let h = startHistory(loc('runs'))
    h = recordLocation(h, loc('runs', { status: 'failed' }))
    expect(h.entries).toHaveLength(1)
    expect(h.entries[0].params).toEqual({ status: 'failed' })

    h = recordLocation(h, loc('runs', { status: 'failed', run: 'r-42', tab: 'live' }))
    h = recordLocation(h, loc('runs', { run: 'r-42', tab: 'evidence' }))
    expect(keys(h)).toEqual(['runs', 'runs|run=r-42|tab=live', 'runs|run=r-42|tab=evidence'])
    expect(h.index).toBe(2)
    expect(canStep(h, -1)).toBe(true)
    expect(canStep(h, 1)).toBe(false)
  })

  it('a new place after Back drops what was ahead, as a browser does', () => {
    let h = startHistory(loc('start'))
    h = recordLocation(h, loc('runs'))
    h = recordLocation(h, loc('proof'))
    h = { ...h, index: 0 }
    h = recordLocation(h, loc('library'))
    expect(keys(h)).toEqual(['start', 'library'])
    expect(canStep(h, 1)).toBe(false)
  })

  it('a redirect stub is replaced by the page it forwards to', () => {
    const isRedirect = (id) => id === 'agents'
    let h = startHistory(loc('runs'))
    h = recordLocation(h, loc('agents'), { isRedirect })
    h = recordLocation(h, loc('component-agents', { tab: 'fleet' }), { isRedirect })
    expect(keys(h)).toEqual(['runs', 'component-agents|tab=fleet'])
  })

  it('one address of a live page can be a stub: a run’s old detection tab is replaced by Results', () => {
    const isRedirect = (id, params) => id === 'runs' && !!runsTabRedirect(params)
    let h = startHistory(loc('library'))
    h = recordLocation(h, loc('runs', { run: 'r-42', tab: 'evidence' }), { isRedirect })
    h = recordLocation(h, loc('proof', { run: 'r-42', tab: 'evidence' }), { isRedirect })
    expect(keys(h)).toEqual(['library', 'proof|run=r-42|tab=evidence'])
    // The run's own page is no stub: it stays in the history.
    h = recordLocation(h, loc('runs', { run: 'r-42' }), { isRedirect })
    h = recordLocation(h, loc('proof', { run: 'r-42' }), { isRedirect })
    expect(keys(h)).toEqual(['library', 'proof|run=r-42|tab=evidence', 'runs|run=r-42', 'proof|run=r-42'])
  })

  it("the browser's own Back onto the previous entry moves there instead of adding a step", () => {
    let h = startHistory(loc('runs'))
    h = recordLocation(h, loc('proof'))
    h = recordLocation(h, loc('runs'), { traversal: true })
    expect(keys(h)).toEqual(['runs', 'proof'])
    expect(h.index).toBe(0)
    expect(canStep(h, 1)).toBe(true)
  })

  it(`keeps at most ${HISTORY_LIMIT} places`, () => {
    let h = startHistory(loc('runs', { run: 'r-0' }))
    for (let i = 1; i <= HISTORY_LIMIT + 5; i++) h = recordLocation(h, loc('runs', { run: `r-${i}` }))
    expect(h.entries).toHaveLength(HISTORY_LIMIT)
    expect(h.index).toBe(HISTORY_LIMIT - 1)
    expect(h.entries[h.index].params.run).toBe(`r-${HISTORY_LIMIT + 5}`)
  })
})

describe('useConsoleRouter — Back / Forward', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '#/runs')
  })

  const mount = (opts = {}) => renderHook(() => useConsoleRouter({ defaultDestination: 'start', ...opts }))

  it('starts with nowhere to go either way', () => {
    const { result } = mount()
    expect(result.current.destination).toBe('runs')
    expect(result.current.canBack).toBe(false)
    expect(result.current.canForward).toBe(false)
  })

  it('walks back and forward through pages, and the URL follows', async () => {
    const { result } = mount()
    act(() => result.current.navigate('proof'))
    await waitFor(() => expect(result.current.destination).toBe('proof'))
    expect(result.current.canBack).toBe(true)
    expect(result.current.canForward).toBe(false)

    act(() => result.current.back())
    expect(result.current.destination).toBe('runs')
    expect(window.location.hash).toBe('#/runs')
    expect(result.current.canBack).toBe(false)
    expect(result.current.canForward).toBe(true)

    act(() => result.current.forward())
    expect(result.current.destination).toBe('proof')
    expect(window.location.hash).toBe('#/proof')
    expect(result.current.canForward).toBe(false)
  })

  it('records a run and a tab opened with setParams, which writes no browser entry', () => {
    const { result } = mount()
    act(() => result.current.setParams({ run: 'r-42', tab: 'live' }, { replace: true }))
    act(() => result.current.setParams({ tab: 'evidence' }))
    expect(result.current.canBack).toBe(true)

    act(() => result.current.back())
    expect(result.current.params).toEqual({ run: 'r-42', tab: 'live' })
    expect(window.location.hash).toBe('#/runs?run=r-42&tab=live')

    act(() => result.current.back())
    expect(result.current.params).toEqual({})
    expect(result.current.canBack).toBe(false)
  })

  it('a list filter is not a step: Back returns to the list as it was last filtered', () => {
    const { result } = mount()
    act(() => result.current.setParams({ status: 'failed' }))
    act(() => result.current.setParams({ q: 'edr' }))
    expect(result.current.canBack).toBe(false)

    act(() => result.current.setParams({ run: 'r-7', tab: 'live' }, { replace: true }))
    act(() => result.current.back())
    expect(result.current.params).toEqual({ status: 'failed', q: 'edr' })
  })

  it('a new navigation after Back leaves nothing ahead', async () => {
    const { result } = mount()
    act(() => result.current.navigate('proof'))
    await waitFor(() => expect(result.current.destination).toBe('proof'))
    act(() => result.current.back())
    expect(result.current.canForward).toBe(true)

    act(() => result.current.navigate('library'))
    await waitFor(() => expect(result.current.destination).toBe('library'))
    expect(result.current.canForward).toBe(false)
    act(() => result.current.back())
    expect(result.current.destination).toBe('runs')
  })

  it('Back never lands on a redirect stub', async () => {
    const { result } = mount({ isRedirect: (id) => id === 'agents' })
    act(() => result.current.navigate('agents'))
    await waitFor(() => expect(result.current.destination).toBe('agents'))
    act(() => result.current.navigate('component-agents', { tab: 'fleet' }))
    await waitFor(() => expect(result.current.destination).toBe('component-agents'))

    act(() => result.current.back())
    expect(result.current.destination).toBe('runs')
  })

  it('Back never lands on an old run tab link that forwarded to Results', async () => {
    const { result } = mount({ isRedirect: (id, params) => id === 'runs' && !!runsTabRedirect(params) })
    act(() => result.current.navigate('runs', { run: 'r-42', tab: 'storyline' }))
    await waitFor(() => expect(result.current.params.tab).toBe('storyline'))
    act(() => result.current.navigate('proof', { run: 'r-42', tab: 'storyline' }))
    await waitFor(() => expect(result.current.destination).toBe('proof'))

    act(() => result.current.back())
    expect(result.current.destination).toBe('runs')
    expect(result.current.params).toEqual({})
  })

  it("the browser's own Back keeps the header's Forward in step", async () => {
    const { result } = mount()
    act(() => result.current.navigate('proof'))
    await waitFor(() => expect(result.current.destination).toBe('proof'))

    act(() => {
      window.history.replaceState(null, '', '#/runs')
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    expect(result.current.destination).toBe('runs')
    expect(result.current.canBack).toBe(false)
    expect(result.current.canForward).toBe(true)
  })
})

describe('resultTabs — the tabs of Results, and where a run’s old tab links go', () => {
  it('lists the six tabs in order', () => {
    expect(RESULT_TABS.map((t) => t.label)).toEqual([
      'Detections', 'Storyline', 'Techniques', 'Topology', 'Causality', 'Tenant evidence',
    ])
  })

  it('reads ?tab=, with evidence as the old name for Detections', () => {
    expect(resultTab('evidence')).toBe('detections')
    expect(resultTab('topology')).toBe('topology')
    expect(resultTab('tenant')).toBe('tenant')
    expect(resultTab(undefined)).toBe('detections')
    expect(resultTab('live')).toBe('detections')
  })

  it('forwards only a run link that names a moved tab', () => {
    expect(runsTabRedirect({ run: 'r-1', tab: 'causality' })).toEqual({ run: 'r-1', tab: 'causality' })
    expect(runsTabRedirect({ open: 'r-1', tab: 'evidence' })).toEqual({ run: 'r-1', tab: 'evidence' })
    expect(runsTabRedirect({ run: 'r-1', tab: 'live' })).toBeNull()
    expect(runsTabRedirect({ run: 'r-1' })).toBeNull()
    expect(runsTabRedirect({ tab: 'evidence' })).toBeNull()
    expect(runsTabRedirect(undefined)).toBeNull()
  })
})
