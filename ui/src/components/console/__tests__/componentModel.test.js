import { describe, it, expect } from 'vitest'
import { COMPONENTS, componentStatuses, issueCount } from '../componentModel.js'

/**
 * Each component's status comes from what SimCore actually holds, or it reads
 * Unknown / "you confirmed". The rail dot, the "N issues" count and the
 * Components page all read this one model, so a component cannot be green in
 * one place and unknown in another.
 */

const healthy = { reachable: true, overall: 'ok', degraded: [], gaps: [], components: [], version: '1.2.0' }
const online = (id) => ({ agent_id: id, hostname: id, status: 'online' })
const offline = (id) => ({ agent_id: id, hostname: id, status: 'offline' })

function statuses(over = {}) {
  return componentStatuses({
    healthModel: healthy,
    agents: [], agentsLoaded: true, agentsError: null,
    tenants: [], tenantsLoaded: true, tenantsError: null,
    collectorTargets: [], collectorError: null,
    firewalls: [], firewallError: null,
    confirmed: {},
    ...over,
  })
}

describe('the eight components', () => {
  it('are listed in the product order the rail shows', () => {
    expect(COMPONENTS.map((c) => c.name)).toEqual([
      'POVengine', 'Agents', 'Tenant', 'HTTP collector', 'Broker VM', 'Firewall', 'XDR Collector', 'Cortex Engine',
    ])
  })

  it('every one carries a real rail icon', () => {
    for (const c of COMPONENTS) expect(c.icon, c.key).toMatch(/^[a-z-]+$/)
  })
})

describe('POVengine reads /api/health', () => {
  it('is Unknown before the first answer', () => {
    expect(statuses({ healthModel: null }).pov.status).toBe('unknown')
  })
  it('is Down when SimCore did not answer', () => {
    expect(statuses({ healthModel: { reachable: false, degraded: [], gaps: [] } }).pov.status).toBe('down')
  })
  it('is Degraded and names the components that are not ok', () => {
    const s = statuses({ healthModel: { ...healthy, overall: 'degraded', degraded: ['adapter_catalog'] } }).pov
    expect(s.status).toBe('degraded')
    expect(s.detail).toMatch(/adapter_catalog/)
  })
  it('is Up when every reported component is ok', () => {
    expect(statuses().pov.status).toBe('up')
  })
})

describe('Agents read the agent list', () => {
  it('is Off when no agent has enrolled', () => {
    expect(statuses().agent.status).toBe('off')
  })
  it('is Unknown while the list has not arrived, never Off', () => {
    expect(statuses({ agentsLoaded: false }).agent.status).toBe('unknown')
    expect(statuses({ agentsError: 'boom' }).agent.status).toBe('unknown')
  })
  it('is Up when every agent is online', () => {
    expect(statuses({ agents: [online('a'), online('b')] }).agent.status).toBe('up')
  })
  it('is Degraded when some are online and some are not', () => {
    const s = statuses({ agents: [online('a'), offline('b')] }).agent
    expect(s.status).toBe('degraded')
    expect(s.detail).toMatch(/1 of 2 online/)
  })
  it('is Down when none is online', () => {
    expect(statuses({ agents: [offline('a')] }).agent.status).toBe('down')
  })
  it('is Degraded, not Down, when the only agents are stale — SimCore calls them stale, not gone', () => {
    const s = statuses({ agents: [{ agent_id: 'a', status: 'stale' }] }).agent
    expect(s.status).toBe('degraded')
    expect(s.detail).toMatch(/stale/)
  })
})

describe('Tenant reads the last test of each registered tenant', () => {
  it('is Off when none is connected', () => {
    expect(statuses().tenant.status).toBe('off')
  })
  it('is Up only on a test that passed', () => {
    expect(statuses({ tenants: [{ name: 't', last_verified_ok: true, last_verified_at: '2026-11-13T10:00:00' }] }).tenant.status).toBe('up')
  })
  it('is Down on a test that failed, with its error', () => {
    const s = statuses({ tenants: [{ name: 't', last_verified_ok: false, last_verified_error: 'HTTP 401' }] }).tenant
    expect(s.status).toBe('down')
    expect(s.detail).toMatch(/401/)
  })
  it('is Unknown when the key was stored but never tested', () => {
    const s = statuses({ tenants: [{ name: 't', last_verified_ok: null }] }).tenant
    expect(s.status).toBe('unknown')
    expect(s.detail).toMatch(/never tested/i)
  })
})

describe('HTTP collector reads the collector targets', () => {
  const target = (verdict) => ({ name: 'c', kind: 'xsiam_http_collector', is_default: true, last_check: verdict ? { verdict } : null })
  it('is Off with no target and no confirmation', () => {
    expect(statuses().http.status).toBe('off')
  })
  it('reads "you confirmed" — never Up — when only the DC said so', () => {
    const s = statuses({ confirmed: { collector: true } }).http
    expect(s.status).toBe('unknown')
    expect(s.word).toBe('You confirmed')
  })
  it('is Up when the collector accepted a test record', () => {
    expect(statuses({ collectorTargets: [target('accepted')] }).http.status).toBe('up')
  })
  it('is Degraded when it was only reachable', () => {
    expect(statuses({ collectorTargets: [target('reachable')] }).http.status).toBe('degraded')
  })
  it('is Down when it refused or could not be reached', () => {
    expect(statuses({ collectorTargets: [target('refused')] }).http.status).toBe('down')
    expect(statuses({ collectorTargets: [target('unreachable')] }).http.status).toBe('down')
  })
  it('is Unknown when never checked, or when the list could not be read', () => {
    expect(statuses({ collectorTargets: [target(null)] }).http.status).toBe('unknown')
    expect(statuses({ collectorTargets: null, collectorError: 'boom' }).http.status).toBe('unknown')
  })
})

describe('Firewall reads the registry', () => {
  it('is Off when none is registered', () => {
    expect(statuses().ngfw.status).toBe('off')
  })
  it('is Down when the last check was blocked', () => {
    expect(statuses({ firewalls: [{ name: 'fw', last_verified_ok: false, last_verified_error: 'NO_LOG_FORWARDING' }] }).ngfw.status).toBe('down')
  })
  it('never reads Up from "not blocked": the stored result cannot tell ok from degraded', () => {
    const s = statuses({ firewalls: [{ name: 'fw', last_verified_ok: true }] }).ngfw
    expect(s.status).toBe('unknown')
    expect(s.word).toBe('Not blocked')
  })
})

describe('components SimCore has no API for', () => {
  it('Broker VM, XDR Collector and Cortex Engine are never green', () => {
    const s = statuses({ confirmed: { bvm: true } })
    for (const k of ['bvm', 'xdrc', 'engine']) expect(s[k].status, k).toBe('unknown')
  })
  it('Broker VM says "you confirmed" once the DC ticked it', () => {
    expect(statuses({ confirmed: { bvm: true } }).bvm.word).toBe('You confirmed')
    expect(statuses().bvm.word).toBe("Can't check")
  })
})

describe('issue count', () => {
  it('counts Degraded and Down only — Off and Unknown are not issues', () => {
    const s = statuses({
      healthModel: { ...healthy, overall: 'degraded', degraded: ['x'] },
      agents: [offline('a')],
    })
    expect(issueCount(s)).toBe(2)
    expect(issueCount(statuses())).toBe(0)
  })
})
