/**
 * runChain.laneDestination — where a lane header on Run detail's chain leads.
 * What each card says is the runtime log's (runtimeLog.test.js).
 */
import { describe, it, expect } from 'vitest'
import { laneDestination } from '../runChain.js'

describe('laneDestination', () => {
  it('opens the component behind each signal path, from the one component table', () => {
    const page = (lane) => laneDestination(lane)?.destination
    expect(page('ENDPOINT')).toBe('component-agents')
    expect(page('WIRE')).toBe('component-firewall')
    expect(page('CLOUD')).toBe('component-tenant')
    expect(page('ENG')).toBe('component-tenant')
    // The HTTP collector has its own page (design v26 slice 4); whatever the
    // table says, the run chain follows it.
    expect(laneDestination('COLLECTOR')).toEqual({ destination: 'component-http' })
  })
})
